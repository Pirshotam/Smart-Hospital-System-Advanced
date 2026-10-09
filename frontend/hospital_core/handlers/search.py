"""Hospital search, comparison, details and the live map."""
from hospital_core import validate as v
from hospital_core.config import STALE_MINUTES
from hospital_core.db import minutes_between, read
from hospital_core.router import ApiError, route
from hospital_core.services import forecasting, matcher

SEARCHERS = ("patient", "coordinator", "admin")


@route("GET", "/services")
def list_services(ctx):
    with read() as c:
        return c.all("SELECT service_id, service_name FROM services ORDER BY service_name")


@route("POST", "/search", roles=SEARCHERS)
def search(ctx):
    b = ctx.body
    params = {
        "resource_type": v.resource(b),
        "latitude": v.number(b, "latitude", -90, 90), "longitude": v.number(b, "longitude", -180, 180),
        "needs_ventilator": v.flag(b, "needs_ventilator"), "require_icu": v.flag(b, "require_icu"),
        "service_id": v.number(b, "service_id", required=False, integer=True),
        "hospital_name": v.text(b, "hospital_name", 0, 100, required=False),
        "max_distance_km": v.number(b, "max_distance_km", 0.0001, 500, required=False),
        "emergency_only": v.flag(b, "emergency_only"),
        "include_unsuitable": v.flag(b, "include_unsuitable", True),
        "urgency": v.urgency(b)}
    with read() as c:
        return matcher.search(c, params)


def _detail(c, hospital_id, lat=None, lon=None):
    h = c.one("""SELECT hospital_id, hospital_name, area, city, location, latitude, longitude, contact,
                        emergency_available FROM hospitals
                 WHERE hospital_id = ? AND verification_status = 'verified' AND account_status = 'active'""",
              (hospital_id,))
    if not h:
        raise ApiError(404, "Hospital not found")
    caps = c.all("""SELECT resource_type, total_capacity, occupied, reserved, unavailable, available, last_updated
                    FROM capacity WHERE hospital_id = ? ORDER BY resource_type""", (hospital_id,))
    for cap in caps:
        cap["minutes_ago"] = minutes_between(cap["last_updated"])
        cap["possibly_outdated"] = cap["minutes_ago"] > STALE_MINUTES
    h["services"] = [r["service_name"] for r in c.all(
        """SELECT s.service_name FROM hospital_services hs JOIN services s ON s.service_id = hs.service_id
           WHERE hs.hospital_id = ? AND hs.is_available = 1 ORDER BY s.service_name""", (hospital_id,))]
    h["capacity"] = caps
    avg, queue = forecasting.wait_inputs(c)
    icu = next((x for x in caps if x["resource_type"] == "icu_bed"), None)
    ratio = icu["occupied"] / icu["total_capacity"] if icu and icu["total_capacity"] else 0.5
    h["est_wait_min"] = forecasting.estimate_wait_min(avg, queue, hospital_id, ratio)
    h["outlook"] = forecasting.capacity_outlook(c, hospital_id)
    if lat is not None:
        d = matcher.haversine_km(lat, lon, h["latitude"], h["longitude"])
        h["distance_km"], h["est_travel_min"] = round(d, 1), matcher.travel_minutes(d)
    return h


@route("GET", "/hospitals/{hospital_id}/detail")
def hospital_detail(ctx):
    with read() as c:
        return _detail(c, ctx.pid("hospital_id"))


@route("POST", "/compare", roles=SEARCHERS)
def compare(ctx):
    ids = ctx.body.get("hospital_ids") or []
    if not (2 <= len(ids) <= 4):
        raise ApiError(422, "Choose between 2 and 4 hospitals to compare")
    lat, lon = v.number(ctx.body, "latitude", -90, 90), v.number(ctx.body, "longitude", -180, 180)
    with read() as c:
        return [_detail(c, int(i), lat, lon) for i in ids]


@route("GET", "/map/hospitals")
def live_map(ctx):
    """Markers for the live hospital map (green = 3+ ICU free, amber = 1-2, red = none)."""
    with read() as c:
        rows = c.all("""SELECT h.hospital_id, h.hospital_name, h.area, h.latitude, h.longitude,
                               h.emergency_available,
                               MAX(CASE WHEN c.resource_type = 'icu_bed' THEN c.available END) AS icu_available,
                               MAX(CASE WHEN c.resource_type = 'general_bed' THEN c.available END) AS general_available,
                               MAX(CASE WHEN c.resource_type = 'emergency_bed' THEN c.available END) AS ed_available,
                               MAX(CASE WHEN c.resource_type = 'ventilator' THEN c.available END) AS vent_available,
                               MIN(c.last_updated) AS oldest_update
                        FROM hospitals h LEFT JOIN capacity c ON c.hospital_id = h.hospital_id
                        WHERE h.verification_status = 'verified' AND h.account_status = 'active'
                        GROUP BY h.hospital_id, h.hospital_name, h.area, h.latitude, h.longitude,
                                 h.emergency_available""")
    for r in rows:
        icu = r["icu_available"] or 0
        r["status"] = "green" if icu >= 3 else ("amber" if icu >= 1 else "red")
        r["minutes_ago"] = minutes_between(r["oldest_update"]) if r["oldest_update"] else None
        r["possibly_outdated"] = (r["minutes_ago"] or 0) > STALE_MINUTES
    return rows

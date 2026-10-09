"""Smart hospital matcher: evaluates every verified hospital, explains why a hospital is not
suitable, and ranks the suitable ones with a weighted 0-100 match score.

Factors (from the brief): required bed type, ICU availability, ventilator availability, distance,
emergency department capacity, required specialist/service, current occupancy, estimated travel
time (derived from distance), plus data freshness."""
import math

from hospital_core.config import AVG_SPEED_KMH, STALE_MINUTES
from hospital_core.db import minutes_between, utc_now
from hospital_core.services import forecasting

MAX_RADIUS_KM = 50
FRESH_MINUTES = 15
W = dict(distance=0.30, availability=0.20, occupancy=0.15, emergency=0.10,
         ventilator=0.10, service=0.05, freshness=0.10)
LABELS = {"general_bed": "general bed", "emergency_bed": "emergency bed", "icu_bed": "ICU bed",
          "nicu_bed": "NICU bed", "ventilator": "ventilator", "operation_theatre": "operation theatre",
          "isolation_bed": "isolation bed", "dialysis": "dialysis slot", "trauma": "trauma capacity",
          "ambulance": "ambulance"}


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def travel_minutes(distance_km):
    return round(distance_km / AVG_SPEED_KMH * 60)


def freshness_score(age_minutes):
    if age_minutes <= FRESH_MINUTES:
        return 1.0
    if age_minutes >= STALE_MINUTES:
        return 0.0
    return 1 - (age_minutes - FRESH_MINUTES) / (STALE_MINUTES - FRESH_MINUTES)


def score_hospital(distance_km, resource_cap, ed_cap, vent_cap, needs_vent,
                   emergency_available, service_ok, age_minutes):
    """Weighted match score (0-100). Inputs are already known to be suitable."""
    dist = max(0.0, 1 - distance_km / MAX_RADIUS_KM)
    avail = min(resource_cap["available"] / 5, 1)
    occ_ratio = resource_cap["occupied"] / resource_cap["total_capacity"] if resource_cap["total_capacity"] else 1
    ed = min(ed_cap["available"] / 3, 1) if (emergency_available and ed_cap) else 0.0
    vent = min(vent_cap["available"] / 3, 1) if (needs_vent and vent_cap) else 1.0
    total = (W["distance"] * dist + W["availability"] * avail + W["occupancy"] * (1 - occ_ratio)
             + W["emergency"] * ed + W["ventilator"] * vent
             + W["service"] * (1.0 if service_ok else 0.0) + W["freshness"] * freshness_score(age_minutes))
    return round(total * 100, 1)


def _load(c):
    hospitals = c.all("""SELECT hospital_id, hospital_name, area, city, location, latitude, longitude, contact,
                                emergency_available FROM hospitals
                         WHERE verification_status = 'verified' AND account_status = 'active'""")
    caps = {}
    for r in c.all("""SELECT hospital_id, resource_type, capacity_id, total_capacity, occupied, reserved,
                             unavailable, available, last_updated FROM capacity"""):
        caps.setdefault(r["hospital_id"], {})[r["resource_type"]] = r
    svcs = {}
    for r in c.all("""SELECT hs.hospital_id, s.service_id, s.service_name FROM hospital_services hs
                      JOIN services s ON s.service_id = hs.service_id WHERE hs.is_available = 1"""):
        svcs.setdefault(r["hospital_id"], {})[r["service_id"]] = r["service_name"]
    return hospitals, caps, svcs


def search(c, p):
    """p: dict with resource_type, latitude, longitude and optional filters. Returns every verified
    hospital with a suitable flag and reasons, best matches first."""
    now = utc_now()
    hospitals, caps, svcs = _load(c)
    avg_resp, queue = forecasting.wait_inputs(c)
    res_type = p["resource_type"]
    res_label = LABELS.get(res_type, res_type)
    out = []
    for h in hospitals:
        if p.get("hospital_name") and p["hospital_name"].lower() not in h["hospital_name"].lower():
            continue
        dist = haversine_km(p["latitude"], p["longitude"], h["latitude"], h["longitude"])
        if p.get("max_distance_km") and dist > p["max_distance_km"]:
            continue
        hc, hs = caps.get(h["hospital_id"], {}), svcs.get(h["hospital_id"], {})
        res_cap, vent_cap = hc.get(res_type), hc.get("ventilator")
        icu_cap, ed_cap = hc.get("icu_bed"), hc.get("emergency_bed")

        reasons = []
        if not res_cap:
            reasons.append(f"No {res_label} listed")
        elif res_cap["available"] < 1:
            reasons.append(f"No {res_label} available")
        if p.get("needs_ventilator") and (not vent_cap or vent_cap["available"] < 1):
            reasons.append("No ventilator available")
        if p.get("require_icu") and (not icu_cap or icu_cap["available"] < 1):
            reasons.append("No ICU bed available")
        if p.get("service_id") and p["service_id"] not in hs:
            reasons.append("Required specialist/service not available")
        if p.get("emergency_only") and not h["emergency_available"]:
            reasons.append("Emergency department not available")

        newest = max((x["last_updated"] for x in hc.values()), default=None)
        oldest = min((x["last_updated"] for x in hc.values()), default=None)
        age = minutes_between(oldest, now) if oldest else 9999
        suitable = not reasons
        occ_ratio = (res_cap["occupied"] / res_cap["total_capacity"]
                     if res_cap and res_cap["total_capacity"] else 1.0)
        service_ok = (not p.get("service_id")) or p["service_id"] in hs
        out.append({
            "hospital_id": h["hospital_id"], "hospital_name": h["hospital_name"], "area": h["area"],
            "contact": h["contact"], "latitude": h["latitude"], "longitude": h["longitude"],
            "distance_km": round(dist, 1), "est_travel_min": travel_minutes(dist),
            "est_wait_min": forecasting.estimate_wait_min(avg_resp, queue, h["hospital_id"], occ_ratio),
            "suitable": suitable, "reasons": reasons,
            "match_percent": score_hospital(dist, res_cap, ed_cap, vent_cap, p.get("needs_ventilator", False),
                                            bool(h["emergency_available"]), service_ok, age) if suitable else None,
            "capacity_id": res_cap["capacity_id"] if res_cap else None,
            "resource_available": res_cap["available"] if res_cap else 0,
            "icu_available": icu_cap["available"] if icu_cap else 0,
            "ventilator_available": vent_cap["available"] if vent_cap else 0,
            "ed_available": ed_cap["available"] if ed_cap else 0,
            "emergency_available": bool(h["emergency_available"]),
            "services": sorted(hs.values()),
            "last_updated_min_ago": minutes_between(newest, now) if newest else None,
            "possibly_outdated": age > STALE_MINUTES,
            "is_best_match": False, "is_nearest": False,
        })
    suitable = sorted([x for x in out if x["suitable"]], key=lambda x: x["match_percent"], reverse=True)
    unsuitable = sorted([x for x in out if not x["suitable"]], key=lambda x: x["distance_km"])
    if suitable:
        suitable[0]["is_best_match"] = True
        min(suitable, key=lambda x: x["distance_km"])["is_nearest"] = True
    return suitable + (unsuitable if p.get("include_unsuitable", True) else [])

"""Hospital staff endpoints. Staff only ever see and change THEIR OWN hospital, and only after an
administrator has verified the hospital."""
from datetime import timedelta

from hospital_core import validate as v
from hospital_core.config import BED_TYPES, STALE_MINUTES
from hospital_core.db import minutes_between, read, ts, tx, utc_now
from hospital_core.router import ApiError, ensure_hospital_verified, route, staff
from hospital_core.services import forecasting, reservations
from hospital_core.services.notifier import notify_admins

STAFF = ("hospital_staff",)


@route("GET", "/hospital/me", roles=STAFF)
def my_hospital(ctx):
    with read() as c:
        return c.one("""SELECT hospital_id, hospital_name, area, city, location, contact, latitude, longitude,
                               verification_status, emergency_available, account_status
                        FROM hospitals WHERE hospital_id = ?""", (staff(ctx),))


@route("GET", "/hospital/capacity", roles=STAFF)
def capacity(ctx):
    with read() as c:
        rows = c.all("""SELECT capacity_id, resource_type, total_capacity, occupied, reserved, unavailable,
                               available, last_updated FROM capacity WHERE hospital_id = ?
                        ORDER BY resource_type""", (staff(ctx),))
    for r in rows:
        r["minutes_ago"] = minutes_between(r["last_updated"])
        r["possibly_outdated"] = r["minutes_ago"] > STALE_MINUTES
    return rows


@route("POST", "/hospital/capacity", roles=STAFF)
def add_resource(ctx):
    hid = staff(ctx)
    resource = v.resource(ctx.body)
    total = v.number(ctx.body, "total_capacity", 0, 100000, integer=True)
    occ = v.number(ctx.body, "occupied", 0, 100000, required=False, default=0, integer=True)
    with tx() as c:
        ensure_hospital_verified(c, hid)
        if occ > total:
            raise ApiError(422, "Occupied cannot exceed total")
        if c.one("SELECT 1 AS x FROM capacity WHERE hospital_id = ? AND resource_type = ?", (hid, resource)):
            raise ApiError(409, "This resource already exists - update it instead")
        cid = c.insert("INSERT INTO capacity (hospital_id, resource_type, total_capacity, occupied, last_updated) "
                       "VALUES (?,?,?,?,?)", (hid, resource, total, occ, ts(utc_now())))
        reservations.log_change(c, cid, ctx.user["user_id"], 0, total, 0, occ, "resource_added")
    return {"ok": True}


@route("PATCH", "/hospital/capacity/{capacity_id}", roles=STAFF)
def update_capacity(ctx):
    hid, cid = staff(ctx), ctx.pid("capacity_id")
    b = ctx.body
    with tx() as c:
        ensure_hospital_verified(c, hid)
        old = c.one("SELECT * FROM capacity WHERE capacity_id = ? AND hospital_id = ?", (cid, hid))
        if not old:
            raise ApiError(404, "Capacity row not found for your hospital")
        total = old["total_capacity"] if b.get("total_capacity") is None else v.number(b, "total_capacity", 0, 100000, integer=True)
        occ = old["occupied"] if b.get("occupied") is None else v.number(b, "occupied", 0, 100000, integer=True)
        unav = old["unavailable"] if b.get("unavailable") is None else v.number(b, "unavailable", 0, 100000, integer=True)
        if occ + old["reserved"] + unav > total:
            raise ApiError(422, f"Occupied + reserved ({old['reserved']}) + unavailable cannot exceed total")
        flagged = abs(occ - old["occupied"]) > max(5, 0.5 * total)    # sudden big jump -> admin review
        c.run("UPDATE capacity SET total_capacity = ?, occupied = ?, unavailable = ?, last_updated = ? "
              "WHERE capacity_id = ?", (total, occ, unav, ts(utc_now()), cid))
        reservations.log_change(c, cid, ctx.user["user_id"], old["total_capacity"], total,
                                old["occupied"], occ, "manual_update", flagged)
        if flagged:
            notify_admins(c, "Suspicious capacity update",
                          f"{old['resource_type']} occupancy changed {old['occupied']} -> {occ} "
                          f"(hospital #{hid}). Review it in the admin panel.", "alert")
        return c.one("SELECT * FROM capacity WHERE capacity_id = ?", (cid,))


@route("GET", "/hospital/services", roles=STAFF)
def services(ctx):
    with read() as c:
        return c.all("""SELECT s.service_id, s.service_name, COALESCE(hs.is_available, 0) AS is_available,
                               (hs.service_id IS NOT NULL) AS offered
                        FROM services s LEFT JOIN hospital_services hs
                          ON hs.service_id = s.service_id AND hs.hospital_id = ?
                        ORDER BY s.service_name""", (staff(ctx),))


@route("PUT", "/hospital/services/{service_id}", roles=STAFF)
def set_service(ctx):
    hid, sid = staff(ctx), ctx.pid("service_id")
    with tx() as c:
        ensure_hospital_verified(c, hid)
        if not c.one("SELECT 1 AS x FROM services WHERE service_id = ?", (sid,)):
            raise ApiError(404, "Unknown service")
        c.run("""INSERT INTO hospital_services (hospital_id, service_id, is_available) VALUES (?,?,?)
                 ON CONFLICT(hospital_id, service_id) DO UPDATE SET is_available = excluded.is_available""",
              (hid, sid, 1 if v.flag(ctx.body, "is_available") else 0))
    return {"ok": True}


@route("PATCH", "/hospital/emergency", roles=STAFF)
def set_emergency(ctx):
    hid = staff(ctx)
    with tx() as c:
        ensure_hospital_verified(c, hid)
        c.run("UPDATE hospitals SET emergency_available = ? WHERE hospital_id = ?",
              (1 if v.flag(ctx.body, "emergency_available") else 0, hid))
    return {"ok": True}


@route("GET", "/hospital/requests", roles=STAFF)
def incoming(ctx):
    order = "CASE r.request_status WHEN 'request_sent' THEN 0 WHEN 'hospital_reviewing' THEN 1 " \
            "WHEN 'accepted' THEN 2 WHEN 'patient_transferred' THEN 3 ELSE 4 END"
    with read() as c:
        return c.all(f"""SELECT r.request_id, r.patient_reference, r.required_resource, r.needs_ventilator,
                                r.urgency, r.case_summary, r.request_status, r.rejection_reason, r.created_at,
                                r.ambulance_unit, r.transfer_eta_min, s.service_name AS required_service,
                                (SELECT MAX(expires_at) FROM reservations v
                                 WHERE v.request_id = r.request_id AND v.state = 'active') AS reserved_until
                         FROM referral_requests r LEFT JOIN services s ON s.service_id = r.required_service
                         WHERE r.selected_hospital = ? AND r.request_status <> 'searching'
                         ORDER BY {order}, r.created_at DESC LIMIT 100""", (staff(ctx),))


@route("GET", "/hospital/admitted", roles=STAFF)
def admitted(ctx):
    """Manage current admitted patients."""
    with read() as c:
        return c.all("""SELECT request_id, patient_reference, required_resource, needs_ventilator, urgency,
                               case_summary, admitted_at FROM referral_requests
                        WHERE selected_hospital = ? AND request_status = 'admitted' AND discharged_at IS NULL
                        ORDER BY admitted_at DESC""", (staff(ctx),))


@route("POST", "/hospital/admitted/{request_id}/discharge", roles=STAFF)
def discharge(ctx):
    hid, rid = staff(ctx), ctx.pid("request_id")
    with tx() as c:
        ensure_hospital_verified(c, hid)
        req = c.one("SELECT * FROM referral_requests WHERE request_id = ? AND selected_hospital = ? "
                    "AND request_status = 'admitted' AND discharged_at IS NULL", (rid, hid))
        if not req:
            raise ApiError(404, "No such admitted patient at your hospital")
        reservations.discharge(c, req, ctx.user["user_id"])
    return {"ok": True}


@route("GET", "/hospital/dashboard", roles=STAFF)
def dashboard(ctx):
    hid = staff(ctx)
    with read() as c:
        caps = c.all("SELECT resource_type, total_capacity, occupied, reserved, unavailable, available, "
                     "last_updated FROM capacity WHERE hospital_id = ?", (hid,))
        m = c.one("""SELECT COUNT(*) AS total_requests,
                            SUM(urgency IN ('high','critical')) AS emergency_requests,
                            SUM(request_status IN ('request_sent','hospital_reviewing')) AS pending_review,
                            SUM(accepted_at IS NOT NULL) AS accepted_referrals,
                            SUM(request_status = 'rejected') AS rejected_referrals,
                            AVG((julianday(responded_at) - julianday(created_at)) * 1440) AS avg_response_min
                     FROM referral_requests WHERE selected_hospital = ? AND request_status <> 'searching'""",
                  (hid,))
        daily = [{"day": str(r["day"]), "admissions": int(r["admissions"])} for r in c.all(
            """SELECT DATE(admitted_at) AS day, COUNT(*) AS admissions FROM referral_requests
               WHERE selected_hospital = ? AND admitted_at >= ? GROUP BY day ORDER BY day""",
            (hid, ts(utc_now() - timedelta(days=14))))]
    for r in caps:
        r["minutes_ago"] = minutes_between(r["last_updated"])
    beds = [x for x in caps if x["resource_type"] in BED_TYPES]
    total, occ = sum(x["total_capacity"] for x in beds), sum(x["occupied"] for x in beds)
    icu = next((x for x in caps if x["resource_type"] == "icu_bed"), None)
    oldest = max((x["minutes_ago"] for x in caps), default=0)
    return {
        "total_beds": int(total), "occupied_beds": int(occ), "available_beds": int(total - occ),
        "capacity_utilization_pct": round(100 * occ / total, 1) if total else 0,
        "icu_occupancy_pct": round(100 * icu["occupied"] / icu["total_capacity"], 1)
        if icu and icu["total_capacity"] else None,
        "emergency_requests": int(m["emergency_requests"] or 0), "total_requests": int(m["total_requests"] or 0),
        "pending_review": int(m["pending_review"] or 0),
        "accepted_referrals": int(m["accepted_referrals"] or 0),
        "rejected_referrals": int(m["rejected_referrals"] or 0),
        "avg_response_min": round(float(m["avg_response_min"]), 1) if m["avg_response_min"] is not None else None,
        "daily_admissions": daily, "resources": caps,
        "data_outdated": oldest > STALE_MINUTES, "oldest_update_min": int(oldest)}


@route("GET", "/hospital/outlook", roles=STAFF)
def outlook(ctx):
    with read() as c:
        return forecasting.capacity_outlook(c, staff(ctx))


@route("GET", "/hospital/trends", roles=STAFF)
def trends(ctx):
    days = int(ctx.params.get("days", 14))
    with read() as c:
        return forecasting.daily_trends(c, min(max(days, 1), 60), staff(ctx))

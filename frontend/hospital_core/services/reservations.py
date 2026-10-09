"""Reservation + capacity logic. Run inside ONE write transaction (db.tx()).

How a bed moves:  available --(referral sent)--> reserved (held 15 min)
                  --(accepted: hold restarts)--> reserved --(patient transferred)--> occupied
                  --(discharged)--> available.   Rejected / cancelled / expired -> available again."""
from datetime import timedelta

from hospital_core.config import ACCEPT_HOLD_MIN, PENDING_HOLD_MIN
from hospital_core.db import ts, utc_now
from hospital_core.services.notifier import notify

NOTIFY_CREATOR = {
    "accepted": ("Referral accepted", "{hospital} accepted request #{id} ({ref}). Transfer the patient "
                 f"within {ACCEPT_HOLD_MIN} minutes or the bed is released."),
    "rejected": ("Referral rejected", "{hospital} rejected request #{id} ({ref}). {note}"),
    "expired": ("Request expired", "Request #{id} ({ref}) expired and the bed was released."),
    "patient_transferred": ("Transfer confirmed", "Patient {ref} reached {hospital} (request #{id})."),
    "admitted": ("Patient admitted", "Patient {ref} was admitted at {hospital} (request #{id})."),
    "hospital_reviewing": ("Hospital reviewing", "{hospital} is reviewing request #{id} ({ref})."),
}
STATUS_COLUMNS = {   # extra timestamp columns set when a status is reached
    "accepted": "accepted_at = :now, responded_at = COALESCE(responded_at, :now)",
    "rejected": "responded_at = COALESCE(responded_at, :now)",
    "patient_transferred": "transferred_at = :now",
    "admitted": "admitted_at = :now",
}


def log_change(c, capacity_id, user_id, old_total, new_total, old_occ, new_occ, reason, flagged=False):
    c.run("""INSERT INTO capacity_audit_log (capacity_id, changed_by, old_total, new_total, old_occupied,
                 new_occupied, reason, flagged, changed_at) VALUES (?,?,?,?,?,?,?,?,?)""",
          (capacity_id, user_id, old_total, new_total, old_occ, new_occ, reason, 1 if flagged else 0,
           ts(utc_now())))


def set_status(c, request_id, status, user_id=None, note=None):
    now = ts(utc_now())
    extra = STATUS_COLUMNS.get(status)
    c.run("UPDATE referral_requests SET request_status = :status" + (", " + extra if extra else "")
          + " WHERE request_id = :rid", {"status": status, "now": now, "rid": request_id})
    c.run("INSERT INTO request_status_history (request_id, status, note, changed_by, changed_at) "
          "VALUES (?,?,?,?,?)", (request_id, status, note, user_id, now))
    if status in NOTIFY_CREATOR:
        r = c.one("""SELECT r.created_by, r.patient_reference, h.hospital_name FROM referral_requests r
                     LEFT JOIN hospitals h ON h.hospital_id = r.selected_hospital WHERE r.request_id = ?""",
                  (request_id,))
        if r and r["created_by"] != user_id:
            title, tmpl = NOTIFY_CREATOR[status]
            notify(c, r["created_by"], title,
                   tmpl.format(hospital=r["hospital_name"] or "The hospital", id=request_id,
                               ref=r["patient_reference"], note=note or ""), "status", request_id)


def reserve_for_request(c, req, hospital_id, minutes=PENDING_HOLD_MIN):
    """Hold one bed (and one ventilator if needed). Returns {'expires_at': ...} or None if full.
    Because writers are serialized, two requests can never take the same last bed."""
    wanted = {req["required_resource"]}
    if req["needs_ventilator"]:
        wanted.add("ventilator")
    found = {r["resource_type"]: r["capacity_id"] for r in
             c.all("SELECT capacity_id, resource_type FROM capacity WHERE hospital_id = ?", (hospital_id,))
             if r["resource_type"] in wanted}
    if set(found) != wanted:
        return None
    ids = sorted(found.values())
    for cid in ids:
        if c.one("SELECT available FROM capacity WHERE capacity_id = ?", (cid,))["available"] < 1:
            return None
    now = utc_now()
    expires = ts(now + timedelta(minutes=minutes))
    for cid in ids:
        c.run("UPDATE capacity SET reserved = reserved + 1, last_updated = ? WHERE capacity_id = ?",
              (ts(now), cid))
        c.run("INSERT INTO reservations (request_id, capacity_id, reserved_at, expires_at) VALUES (?,?,?,?)",
              (req["request_id"], cid, ts(now), expires))
    return {"expires_at": expires}


def extend_hold(c, request_id, minutes=ACCEPT_HOLD_MIN):
    c.run("UPDATE reservations SET expires_at = ? WHERE request_id = ? AND state = 'active'",
          (ts(utc_now() + timedelta(minutes=minutes)), request_id))


def _active(c, request_id):
    return c.all("SELECT reservation_id, capacity_id, units FROM reservations "
                 "WHERE request_id = ? AND state = 'active' ORDER BY capacity_id", (request_id,))


def release(c, request_id, new_state="released"):
    now = ts(utc_now())
    for r in _active(c, request_id):
        c.run("UPDATE reservations SET state = ? WHERE reservation_id = ?", (new_state, r["reservation_id"]))
        c.run("UPDATE capacity SET reserved = MAX(reserved - ?, 0), last_updated = ? WHERE capacity_id = ?",
              (r["units"], now, r["capacity_id"]))


def confirm_transfer(c, request_id, user_id):
    """Patient arrived: reserved -> occupied. False if the hold already expired."""
    rows = _active(c, request_id)
    if not rows:
        return False
    now = ts(utc_now())
    for r in rows:
        old = c.one("SELECT total_capacity, occupied FROM capacity WHERE capacity_id = ?", (r["capacity_id"],))
        c.run("UPDATE capacity SET reserved = MAX(reserved - ?, 0), occupied = occupied + ?, last_updated = ? "
              "WHERE capacity_id = ?", (r["units"], r["units"], now, r["capacity_id"]))
        c.run("UPDATE reservations SET state = 'confirmed' WHERE reservation_id = ?", (r["reservation_id"],))
        log_change(c, r["capacity_id"], user_id, old["total_capacity"], old["total_capacity"],
                   old["occupied"], old["occupied"] + r["units"], "referral_transfer_confirmed")
    return True


def discharge(c, req, user_id):
    """Free the bed (and ventilator) of an admitted patient."""
    rows = c.all("SELECT capacity_id, units FROM reservations WHERE request_id = ? AND state = 'confirmed' "
                 "ORDER BY capacity_id", (req["request_id"],))
    if not rows:   # patients admitted before the system was used (e.g. demo data)
        row = c.one("SELECT capacity_id FROM capacity WHERE hospital_id = ? AND resource_type = ?",
                    (req["selected_hospital"], req["required_resource"]))
        rows = [{"capacity_id": row["capacity_id"], "units": 1}] if row else []
    now = ts(utc_now())
    for r in rows:
        old = c.one("SELECT total_capacity, occupied FROM capacity WHERE capacity_id = ?", (r["capacity_id"],))
        new_occ = max(old["occupied"] - r["units"], 0)
        c.run("UPDATE capacity SET occupied = ?, last_updated = ? WHERE capacity_id = ?",
              (new_occ, now, r["capacity_id"]))
        log_change(c, r["capacity_id"], user_id, old["total_capacity"], old["total_capacity"],
                   old["occupied"], new_occ, "patient_discharged")
    c.run("UPDATE referral_requests SET discharged_at = ? WHERE request_id = ?", (now, req["request_id"]))


def expire_stale(c):
    """Free holds whose time ran out. Returns how many requests expired."""
    ids = [r["request_id"] for r in c.all(
        "SELECT DISTINCT request_id FROM reservations WHERE state = 'active' AND expires_at < ?",
        (ts(utc_now()),))]
    for rid in ids:
        release(c, rid, "expired")
        row = c.one("SELECT request_status FROM referral_requests WHERE request_id = ?", (rid,))
        if row and row["request_status"] in ("request_sent", "hospital_reviewing", "accepted"):
            set_status(c, rid, "expired", None, "Hold time ran out")
    return len(ids)

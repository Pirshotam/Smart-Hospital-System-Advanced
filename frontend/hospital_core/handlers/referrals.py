"""Referral workflow:
Searching -> Request Sent -> Hospital Reviewing -> Accepted -> Patient Transferred -> Admitted
plus Rejected / Cancelled / Expired / No Capacity. Each action checks WHO is allowed to do it."""
import re
import secrets

from hospital_core import validate as v
from hospital_core.config import ACCEPT_HOLD_MIN, AVG_SPEED_KMH
from hospital_core.db import read, ts, tx, utc_now
from hospital_core.router import ApiError, ensure_hospital_verified, route, staff
from hospital_core.services import ai_tools, reservations
from hospital_core.services.matcher import haversine_km
from hospital_core.services.notifier import notify_hospital_staff
from hospital_core.services.tracking import tracking_state

REQUESTERS = ("patient", "coordinator")
ALLOWED = {
    "searching": {"request_sent", "no_capacity", "cancelled"},
    "no_capacity": {"request_sent", "cancelled"},
    "request_sent": {"hospital_reviewing", "accepted", "rejected", "cancelled", "expired"},
    "hospital_reviewing": {"accepted", "rejected", "cancelled", "expired"},
    "accepted": {"patient_transferred", "cancelled", "expired"},
    "patient_transferred": {"admitted"},
}


def get_req(c, request_id):
    req = c.one("SELECT * FROM referral_requests WHERE request_id = ?", (request_id,))
    if not req:
        raise ApiError(404, "Request not found")
    return req


def check_transition(req, new_status):
    if new_status not in ALLOWED.get(req["request_status"], set()):
        raise ApiError(409, f"Cannot move from '{req['request_status']}' to '{new_status}'")


def must_own(req, user):
    if user["role"] != "admin" and req["created_by"] != user["user_id"]:
        raise ApiError(403, "This request belongs to someone else")


def must_be_hospital_of(c, req, user):
    """Only staff of the hospital the request was sent to (and whose hospital is verified)."""
    if user["role"] != "hospital_staff" or not req["selected_hospital"] \
            or user["hospital_id"] != req["selected_hospital"]:
        raise ApiError(403, "This request was not sent to your hospital")
    ensure_hospital_verified(c, user["hospital_id"])


# ------------------------------------------------------------------ create / send / list
@route("POST", "/requests", roles=REQUESTERS)
def create_request(ctx):
    b = ctx.body
    case_text = v.text(b, "case_text", 0, 5000, required=False)
    summary = ai_tools.summarize(case_text)["summary_text"] if case_text else None
    code = secrets.token_hex(5).upper()
    with tx() as c:
        rid = c.insert(
            """INSERT INTO referral_requests (request_code, patient_reference, created_by, required_resource,
                   needs_ventilator, required_service, location, latitude, longitude, urgency, case_text,
                   case_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, v.text(b, "patient_reference", 2, 100), ctx.user["user_id"], v.resource(b),
             1 if v.flag(b, "needs_ventilator") else 0, v.number(b, "service_id", required=False, integer=True),
             v.text(b, "location_label", 0, 255, required=False), v.number(b, "latitude", -90, 90),
             v.number(b, "longitude", -180, 180), v.urgency(b), case_text, summary, ts(utc_now())))
        reservations.set_status(c, rid, "searching", ctx.user["user_id"])
    return {"request_id": rid, "request_code": code, "status": "searching", "case_summary": summary}


@route("GET", "/requests/mine", roles=REQUESTERS)
def my_requests(ctx):
    with read() as c:
        return c.all("""SELECT r.request_id, r.patient_reference, r.required_resource, r.needs_ventilator,
                               r.urgency, r.request_status, r.created_at, h.hospital_name,
                               (SELECT MAX(expires_at) FROM reservations v
                                WHERE v.request_id = r.request_id AND v.state = 'active') AS reserved_until
                        FROM referral_requests r LEFT JOIN hospitals h ON h.hospital_id = r.selected_hospital
                        WHERE r.created_by = ? ORDER BY r.created_at DESC, r.request_id DESC LIMIT 100""",
                     (ctx.user["user_id"],))


def _extract_code(raw):
    """The QR contains a link (...?referral=CODE); staff may also type the bare code."""
    raw = (raw or "").strip()
    found = re.search(r"referral=([A-Za-z0-9]+)", raw)
    return (found.group(1) if found else raw.replace("REFERRAL:", "")).strip().upper()


@route("GET", "/requests/lookup", roles=("hospital_staff",))
def lookup_code(ctx):
    """What does this QR code say? Works for any status, so scanning always shows something useful."""
    staff(ctx)
    code = _extract_code(ctx.params.get("code"))
    if len(code) < 4:
        raise ApiError(422, "Enter the referral code shown under the QR code")
    with read() as c:
        req = c.one("""SELECT r.*, s.service_name AS service FROM referral_requests r
                       LEFT JOIN services s ON s.service_id = r.required_service WHERE r.request_code = ?""",
                    (code,))
        if not req:
            raise ApiError(404, "Unknown referral code - check the code and try again")
        must_be_hospital_of(c, req, ctx.user)
    status = req["request_status"]
    messages = {
        "accepted": "The referral is accepted and the patient is on the way. Confirm when they arrive.",
        "request_sent": "Not accepted yet - accept the referral first, then confirm the arrival.",
        "hospital_reviewing": "Not accepted yet - accept the referral first, then confirm the arrival.",
        "patient_transferred": "Arrival was already confirmed. You can now admit the patient.",
        "admitted": "This patient was already admitted"
                    + (" and has since been discharged." if req["discharged_at"] else "."),
    }
    return {"request_id": req["request_id"], "request_code": code, "patient_reference": req["patient_reference"],
            "required_resource": req["required_resource"], "needs_ventilator": bool(req["needs_ventilator"]),
            "urgency": req["urgency"], "service": req["service"], "case_summary": req["case_summary"],
            "request_status": status, "created_at": req["created_at"], "accepted_at": req["accepted_at"],
            "transferred_at": req["transferred_at"], "admitted_at": req["admitted_at"],
            "can_confirm": status == "accepted",
            "message": messages.get(status, f"This referral is {status.replace('_', ' ')}.")}


@route("POST", "/requests/confirm-qr", roles=("hospital_staff",))
def confirm_by_qr(ctx):
    """Hospital enters the referral code shown as a QR to confirm the patient arrived."""
    staff(ctx)
    code = _extract_code(v.text(ctx.body, "code", 4, 300))
    with tx() as c:
        row = c.one("SELECT request_id FROM referral_requests WHERE request_code = ?", (code,))
        if not row:
            raise ApiError(404, "Unknown referral code - check the code and try again")
        req = get_req(c, row["request_id"])
        must_be_hospital_of(c, req, ctx.user)
        status = req["request_status"]
        if status in ("patient_transferred", "admitted"):
            raise ApiError(409, "Arrival was already confirmed for this request")
        if status in ("request_sent", "hospital_reviewing"):
            raise ApiError(409, "Accept the referral first, then confirm the arrival")
        if status != "accepted":
            raise ApiError(409, f"This referral cannot be confirmed (status: {status.replace('_', ' ')})")
        if not reservations.confirm_transfer(c, req["request_id"], ctx.user["user_id"]):
            raise ApiError(409, "The bed hold expired and the capacity was released")
        reservations.set_status(c, req["request_id"], "patient_transferred", ctx.user["user_id"],
                                "Confirmed by QR code")
    return {"status": "patient_transferred", "request_id": req["request_id"]}


@route("POST", "/requests/{request_id}/send", roles=REQUESTERS)
def send_request(ctx):
    """Send the referral and hold a bed for 15 minutes so nobody else is assigned the same bed."""
    request_id = ctx.pid("request_id")
    hospital_id = v.number(ctx.body, "hospital_id", integer=True)
    held = None
    with tx() as c:
        req = get_req(c, request_id)
        must_own(req, ctx.user)
        check_transition(req, "request_sent")
        hosp = c.one("""SELECT hospital_id, hospital_name FROM hospitals WHERE hospital_id = ?
                        AND verification_status = 'verified' AND account_status = 'active'""", (hospital_id,))
        if not hosp:
            raise ApiError(404, "That hospital is not available")
        held = reservations.reserve_for_request(c, req, hospital_id)
        if held:
            c.run("UPDATE referral_requests SET selected_hospital = ?, match_score = ?, rejection_reason = NULL "
                  "WHERE request_id = ?", (hospital_id, ctx.body.get("match_score"), request_id))
            reservations.set_status(c, request_id, "request_sent", ctx.user["user_id"],
                                    f"Sent to {hosp['hospital_name']}")
            notify_hospital_staff(
                c, hospital_id, "New referral",
                f"Request #{request_id}: {req['required_resource'].replace('_', ' ')}"
                f"{' + ventilator' if req['needs_ventilator'] else ''}, urgency {req['urgency']}.",
                "referral", request_id)
        else:
            reservations.set_status(c, request_id, "no_capacity", ctx.user["user_id"],
                                    f"No capacity at {hosp['hospital_name']}")
    if not held:   # raised after the transaction so the 'no_capacity' status is saved
        raise ApiError(409, "No capacity available at that hospital right now")
    return {"status": "request_sent", "reserved_until": held["expires_at"]}


@route("GET", "/requests/{request_id}")
def request_detail(ctx):
    rid, user = ctx.pid("request_id"), ctx.user
    with read() as c:
        req = get_req(c, rid)
        is_owner = req["created_by"] == user["user_id"]
        is_hospital = (user["role"] == "hospital_staff" and user["hospital_id"]
                       and user["hospital_id"] == req["selected_hospital"])
        if not (is_owner or is_hospital or user["role"] == "admin"):
            raise ApiError(403, "You cannot view this request")
        history = c.all("SELECT status, note, changed_at FROM request_status_history "
                        "WHERE request_id = ? ORDER BY changed_at, history_id", (rid,))
        hospital = None
        if req["selected_hospital"]:
            hospital = c.one("SELECT hospital_name, area, contact, latitude, longitude FROM hospitals "
                             "WHERE hospital_id = ?", (req["selected_hospital"],))
        req["reserved_until"] = c.one("SELECT MAX(expires_at) AS e FROM reservations "
                                      "WHERE request_id = ? AND state = 'active'", (rid,))["e"]
    if not (is_owner or user["role"] == "admin"):
        req.pop("request_code", None)   # only the patient/coordinator shows the QR code
    return {"request": req, "history": history, "hospital": hospital}


# ------------------------------------------------------------------ requester actions
@route("POST", "/requests/{request_id}/cancel", roles=REQUESTERS + ("admin",))
def cancel(ctx):
    rid = ctx.pid("request_id")
    with tx() as c:
        req = get_req(c, rid)
        must_own(req, ctx.user)
        check_transition(req, "cancelled")
        reservations.release(c, rid)
        reservations.set_status(c, rid, "cancelled", ctx.user["user_id"])
        if req["selected_hospital"]:
            notify_hospital_staff(c, req["selected_hospital"], "Referral cancelled",
                                  f"Request #{rid} was cancelled by the sender.", "referral", rid)
    return {"status": "cancelled"}


@route("POST", "/requests/{request_id}/dispatch", roles=("coordinator",))
def dispatch_ambulance(ctx):
    rid = ctx.pid("request_id")
    unit = v.text(ctx.body, "ambulance_unit", 2, 50)
    with tx() as c:
        req = get_req(c, rid)
        must_own(req, ctx.user)
        if req["request_status"] != "accepted":
            raise ApiError(409, "An ambulance can only be dispatched after the hospital accepts")
        h = c.one("SELECT latitude, longitude FROM hospitals WHERE hospital_id = ?", (req["selected_hospital"],))
        dist = haversine_km(req["latitude"], req["longitude"], h["latitude"], h["longitude"])
        eta = max(1, round(dist / AVG_SPEED_KMH * 60))
        c.run("UPDATE referral_requests SET ambulance_unit = ?, dispatched_at = ?, transfer_eta_min = ? "
              "WHERE request_id = ?", (unit, ts(utc_now()), eta, rid))
        notify_hospital_staff(c, req["selected_hospital"], "Ambulance on the way",
                              f"{unit} is bringing the patient for request #{rid} (ETA ~{eta} min).",
                              "referral", rid)
    return {"eta_min": eta}


@route("GET", "/requests/{request_id}/tracking")
def tracking(ctx):
    rid, user = ctx.pid("request_id"), ctx.user
    with read() as c:
        req = get_req(c, rid)
        is_hospital = user["role"] == "hospital_staff" and user["hospital_id"] == req["selected_hospital"]
        if not (req["created_by"] == user["user_id"] or is_hospital or user["role"] == "admin"):
            raise ApiError(403, "You cannot view this request")
        if not req["selected_hospital"]:
            return {"dispatched": False}
        hosp = c.one("SELECT hospital_name, latitude, longitude FROM hospitals WHERE hospital_id = ?",
                     (req["selected_hospital"],))
    return tracking_state(req, hosp)


@route("POST", "/requests/{request_id}/transfer", roles=REQUESTERS + ("hospital_staff",))
def confirm_transfer(ctx):
    """The sender (coordinator/patient) or the receiving hospital confirms the patient arrived."""
    rid = ctx.pid("request_id")
    with tx() as c:
        req = get_req(c, rid)
        if ctx.user["role"] == "hospital_staff":
            must_be_hospital_of(c, req, ctx.user)
        else:
            must_own(req, ctx.user)
        check_transition(req, "patient_transferred")
        if not reservations.confirm_transfer(c, rid, ctx.user["user_id"]):
            raise ApiError(409, "The bed hold expired and the capacity was released")
        reservations.set_status(c, rid, "patient_transferred", ctx.user["user_id"])
    return {"status": "patient_transferred"}


# ------------------------------------------------------------------ hospital actions
@route("POST", "/requests/{request_id}/review", roles=("hospital_staff",))
def start_review(ctx):
    rid = ctx.pid("request_id")
    with tx() as c:
        req = get_req(c, rid)
        must_be_hospital_of(c, req, ctx.user)
        check_transition(req, "hospital_reviewing")
        reservations.set_status(c, rid, "hospital_reviewing", ctx.user["user_id"])
    return {"status": "hospital_reviewing"}


@route("POST", "/requests/{request_id}/accept", roles=("hospital_staff",))
def accept(ctx):
    rid = ctx.pid("request_id")
    with tx() as c:
        req = get_req(c, rid)
        must_be_hospital_of(c, req, ctx.user)
        check_transition(req, "accepted")
        reservations.extend_hold(c, rid, ACCEPT_HOLD_MIN)        # the transfer window starts now
        reservations.set_status(c, rid, "accepted", ctx.user["user_id"])
    return {"status": "accepted", "hold_minutes": ACCEPT_HOLD_MIN}


@route("POST", "/requests/{request_id}/reject", roles=("hospital_staff",))
def reject(ctx):
    rid = ctx.pid("request_id")
    reason = v.text(ctx.body, "reason", 0, 255, required=False)
    with tx() as c:
        req = get_req(c, rid)
        must_be_hospital_of(c, req, ctx.user)
        check_transition(req, "rejected")
        reservations.release(c, rid)
        c.run("UPDATE referral_requests SET rejection_reason = ? WHERE request_id = ?", (reason, rid))
        reservations.set_status(c, rid, "rejected", ctx.user["user_id"], reason)
    return {"status": "rejected"}


@route("POST", "/requests/{request_id}/admit", roles=("hospital_staff",))
def admit(ctx):
    rid = ctx.pid("request_id")
    with tx() as c:
        req = get_req(c, rid)
        must_be_hospital_of(c, req, ctx.user)
        check_transition(req, "admitted")
        reservations.set_status(c, rid, "admitted", ctx.user["user_id"])
    return {"status": "admitted"}

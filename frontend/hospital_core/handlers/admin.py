"""Administrator tools: manage hospitals and users, verify accounts, review suspicious or incorrect
records, monitor security, and reset the demo data."""
from datetime import timedelta

from hospital_core import db, validate as v
from hospital_core.config import STALE_MINUTES
from hospital_core.db import minutes_between, read, ts, tx, utc_now
from hospital_core.router import ApiError, route
from hospital_core.security import hash_password, password_problem
from hospital_core.services import forecasting, reservations
from hospital_core.services.notifier import notify, notify_hospital_staff

ADMIN = ("admin",)
ROLES = ("patient", "coordinator", "hospital_staff", "admin")


# ------------------------------------------------------------------ hospitals
@route("GET", "/admin/hospitals", roles=ADMIN)
def list_hospitals(ctx):
    with read() as c:
        rows = c.all("""SELECT h.hospital_id, h.hospital_name, h.area, h.city, h.location, h.latitude, h.longitude,
                               h.contact, h.verification_status, h.account_status, h.emergency_available,
                               h.created_at,
                               (SELECT COUNT(*) FROM users u WHERE u.hospital_id = h.hospital_id
                                  AND u.role = 'hospital_staff') AS staff_accounts,
                               (SELECT COUNT(*) FROM capacity c WHERE c.hospital_id = h.hospital_id) AS resources,
                               (SELECT MAX(c.last_updated) FROM capacity c WHERE c.hospital_id = h.hospital_id)
                                  AS last_capacity_update
                        FROM hospitals h
                        ORDER BY CASE h.verification_status WHEN 'pending' THEN 0 WHEN 'verified' THEN 1 ELSE 2 END,
                                 h.hospital_name""")
    for r in rows:
        r["minutes_since_update"] = minutes_between(r["last_capacity_update"]) if r["last_capacity_update"] else None
    return rows


@route("POST", "/admin/hospitals", roles=ADMIN)
def create_hospital(ctx):
    b = ctx.body
    verified = v.flag(b, "verified")
    with tx() as c:
        hid = c.insert(
            """INSERT INTO hospitals (hospital_name, area, city, location, latitude, longitude, contact,
                   verification_status, verified_by, verified_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (v.text(b, "hospital_name", 3, 200), v.text(b, "area", 2, 100),
             v.text(b, "city", 0, 100, required=False, default="Hyderabad"), v.text(b, "location", 3, 255),
             v.number(b, "latitude", -90, 90), v.number(b, "longitude", -180, 180),
             v.text(b, "contact", 0, 50, required=False), "verified" if verified else "pending",
             ctx.user["user_id"] if verified else None, ts(utc_now()) if verified else None, ts(utc_now())))
    return {"hospital_id": hid}


@route("PATCH", "/admin/hospitals/{hospital_id}", roles=ADMIN)
def update_hospital(ctx):
    hid, b = ctx.pid("hospital_id"), ctx.body
    fields = {}
    for key, lo, hi in (("hospital_name", 3, 200), ("area", 2, 100), ("location", 3, 255), ("contact", 0, 50)):
        if b.get(key) is not None:
            fields[key] = v.text(b, key, lo, hi)
    for key, lo, hi in (("latitude", -90, 90), ("longitude", -180, 180)):
        if b.get(key) is not None:
            fields[key] = v.number(b, key, lo, hi)
    if b.get("emergency_available") is not None:
        fields["emergency_available"] = 1 if b["emergency_available"] else 0
    if b.get("account_status") is not None:
        fields["account_status"] = v.choice(b, "account_status", ("active", "inactive", "suspended"))
    if not fields:
        raise ApiError(422, "Nothing to update")
    cols = ", ".join(f"{k} = ?" for k in fields)             # keys come from the whitelist above
    with tx() as c:
        if not c.one("SELECT 1 AS x FROM hospitals WHERE hospital_id = ?", (hid,)):
            raise ApiError(404, "Hospital not found")
        c.run(f"UPDATE hospitals SET {cols} WHERE hospital_id = ?", (*fields.values(), hid))
        if fields.get("account_status") == "suspended":
            notify_hospital_staff(c, hid, "Hospital suspended",
                                  "Your hospital account was suspended by an administrator.", "alert")
    return {"ok": True}


def _set_verification(ctx, state):
    hid = ctx.pid("hospital_id")
    with tx() as c:
        h = c.one("SELECT hospital_name FROM hospitals WHERE hospital_id = ?", (hid,))
        if not h:
            raise ApiError(404, "Hospital not found")
        c.run("UPDATE hospitals SET verification_status = ?, verified_by = ?, verified_at = ? WHERE hospital_id = ?",
              (state, ctx.user["user_id"], ts(utc_now()), hid))
        notify_hospital_staff(c, hid, f"Hospital {state}", f"{h['hospital_name']} was {state} by an administrator.",
                              "alert")
    return {"verification_status": state}


@route("POST", "/admin/hospitals/{hospital_id}/verify", roles=ADMIN)
def verify_hospital(ctx):
    return _set_verification(ctx, "verified")


@route("POST", "/admin/hospitals/{hospital_id}/reject", roles=ADMIN)
def reject_hospital(ctx):
    return _set_verification(ctx, "rejected")


@route("GET", "/admin/outlook/{hospital_id}", roles=ADMIN)
def hospital_outlook(ctx):
    with read() as c:
        return forecasting.capacity_outlook(c, ctx.pid("hospital_id"))


# ------------------------------------------------------------------ users & permissions
@route("GET", "/admin/users", roles=ADMIN)
def list_users(ctx):
    sql = ("SELECT u.user_id, u.full_name, u.email, u.phone, u.role, u.status, u.hospital_id, h.hospital_name, "
           "u.last_login, u.created_at FROM users u LEFT JOIN hospitals h ON h.hospital_id = u.hospital_id "
           "WHERE 1 = 1")
    params = []
    if ctx.params.get("role") in ROLES:
        sql += " AND u.role = ?"
        params.append(ctx.params["role"])
    if ctx.params.get("status") in ("pending", "active", "suspended"):
        sql += " AND u.status = ?"
        params.append(ctx.params["status"])
    sql += (" ORDER BY CASE u.status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, "
            "u.created_at DESC, u.user_id DESC LIMIT 300")
    with read() as c:
        return c.all(sql, params)


@route("POST", "/admin/users", roles=ADMIN)
def create_user(ctx):
    b = ctx.body
    email = v.email(b.get("email"))
    role = v.choice(b, "role", ROLES)
    password = b.get("password") or ""
    problem = password_problem(password)
    if problem:
        raise ApiError(422, problem)
    hospital_id = b.get("hospital_id")
    if role == "hospital_staff" and not hospital_id:
        raise ApiError(422, "Hospital staff must be linked to a hospital")
    with tx() as c:
        if c.one("SELECT 1 AS x FROM users WHERE email = ?", (email,)):
            raise ApiError(409, "Email already registered")
        uid = c.insert("""INSERT INTO users (full_name, email, password_hash, role, hospital_id, status,
                              approved_by, approved_at, created_at) VALUES (?,?,?,?,?,'active',?,?,?)""",
                       (v.text(b, "full_name", 2, 150), email, hash_password(password), role,
                        hospital_id if role == "hospital_staff" else None, ctx.user["user_id"],
                        ts(utc_now()), ts(utc_now())))
    return {"user_id": uid}


@route("PATCH", "/admin/users/{user_id}", roles=ADMIN)
def update_user(ctx):
    uid, b = ctx.pid("user_id"), ctx.body
    new_status_in = b.get("status")
    new_role_in = b.get("role")
    if new_status_in is not None:
        v.choice(b, "status", ("pending", "active", "suspended"))
    if new_role_in is not None:
        v.choice(b, "role", ROLES)
    if uid == ctx.user["user_id"] and (new_status_in in ("suspended", "pending")
                                       or (new_role_in and new_role_in != "admin")):
        raise ApiError(400, "You cannot suspend or demote your own account")
    with tx() as c:
        target = c.one("SELECT * FROM users WHERE user_id = ?", (uid,))
        if not target:
            raise ApiError(404, "User not found")
        role = new_role_in or target["role"]
        status = new_status_in or target["status"]
        hospital_id = b.get("hospital_id") if b.get("hospital_id") is not None else target["hospital_id"]
        if role == "hospital_staff" and not hospital_id:
            raise ApiError(422, "Hospital staff must be linked to a hospital")
        if role != "hospital_staff":
            hospital_id = None
        approving = status == "active" and target["status"] != "active"
        c.run("UPDATE users SET role = ?, status = ?, hospital_id = ?, approved_by = ?, approved_at = ? "
              "WHERE user_id = ?",
              (role, status, hospital_id, ctx.user["user_id"] if approving else target["approved_by"],
               ts(utc_now()) if approving else target["approved_at"], uid))
        if target["status"] == "pending" and status == "active":
            notify(c, uid, "Account approved", "An administrator approved your account. You can log in now.")
    return {"ok": True}


@route("POST", "/admin/users/{user_id}/reset-password", roles=ADMIN)
def reset_password(ctx):
    password = ctx.body.get("new_password") or ""
    problem = password_problem(password)
    if problem:
        raise ApiError(422, problem)
    with tx() as c:
        c.run("UPDATE users SET password_hash = ?, failed_logins = 0, locked_until = NULL WHERE user_id = ?",
              (hash_password(password), ctx.pid("user_id")))
    return {"ok": True}


# ------------------------------------------------------------------ review & audit
@route("GET", "/admin/flagged", roles=ADMIN)
def flagged(ctx):
    """Suspicious capacity updates waiting for review."""
    with read() as c:
        return c.all("""SELECT l.log_id, h.hospital_name, c.resource_type, l.old_total, l.new_total,
                               l.old_occupied, l.new_occupied, l.reason, u.full_name AS changed_by, l.changed_at
                        FROM capacity_audit_log l
                        JOIN capacity c ON c.capacity_id = l.capacity_id
                        JOIN hospitals h ON h.hospital_id = c.hospital_id
                        LEFT JOIN users u ON u.user_id = l.changed_by
                        WHERE l.flagged = 1 AND l.reviewed = 0 ORDER BY l.changed_at DESC LIMIT 100""")


@route("POST", "/admin/flagged/{log_id}/review", roles=ADMIN)
def review_flag(ctx):
    log_id = ctx.pid("log_id")
    action = v.choice(ctx.body, "action", ("approve", "revert"))
    with tx() as c:
        log = c.one("SELECT * FROM capacity_audit_log WHERE log_id = ? AND flagged = 1 AND reviewed = 0", (log_id,))
        if not log:
            raise ApiError(404, "Nothing to review")
        if action == "revert":
            cap = c.one("SELECT * FROM capacity WHERE capacity_id = ?", (log["capacity_id"],))
            if log["old_occupied"] + cap["reserved"] + cap["unavailable"] > log["old_total"]:
                raise ApiError(409, "Cannot revert: current holds no longer fit the old numbers")
            c.run("UPDATE capacity SET total_capacity = ?, occupied = ?, last_updated = ? WHERE capacity_id = ?",
                  (log["old_total"], log["old_occupied"], ts(utc_now()), log["capacity_id"]))
            reservations.log_change(c, log["capacity_id"], ctx.user["user_id"], cap["total_capacity"],
                                    log["old_total"], cap["occupied"], log["old_occupied"], "admin_revert")
        c.run("UPDATE capacity_audit_log SET reviewed = 1, reviewed_by = ?, reviewed_at = ?, review_action = ? "
              "WHERE log_id = ?", (ctx.user["user_id"], ts(utc_now()), action, log_id))
    return {"ok": True}


@route("GET", "/admin/records-review", roles=ADMIN)
def records_review(ctx):
    """Inactive or incorrect records that need attention."""
    issues = []
    cutoff = ts(utc_now() - timedelta(days=30))
    with read() as c:
        for h in c.all("""SELECT h.hospital_id, h.hospital_name, h.verification_status, h.account_status,
                                 COUNT(c.capacity_id) AS resources, MAX(c.last_updated) AS last_update
                          FROM hospitals h LEFT JOIN capacity c ON c.hospital_id = h.hospital_id
                          GROUP BY h.hospital_id, h.hospital_name, h.verification_status, h.account_status"""):
            name = h["hospital_name"]
            if h["verification_status"] == "pending":
                issues.append({"type": "Unverified hospital", "record": name, "detail": "Waiting for verification"})
            if h["account_status"] != "active":
                issues.append({"type": "Inactive hospital", "record": name, "detail": h["account_status"]})
            if h["verification_status"] == "verified" and h["resources"] == 0:
                issues.append({"type": "Incorrect record", "record": name,
                               "detail": "Verified but has no capacity data"})
            elif h["verification_status"] == "verified" and h["last_update"] \
                    and minutes_between(h["last_update"]) > STALE_MINUTES:
                issues.append({"type": "Outdated capacity", "record": name,
                               "detail": f"Not updated for {minutes_between(h['last_update'])} minutes"})
        for u in c.all("SELECT full_name, email, role FROM users WHERE status = 'pending'"):
            issues.append({"type": "Pending account", "record": f"{u['full_name']} ({u['email']})",
                           "detail": f"{u['role']} waiting for approval"})
        for u in c.all("""SELECT full_name, email FROM users WHERE status = 'active' AND role <> 'admin'
                          AND (last_login IS NULL OR last_login < ?) AND created_at < ?""", (cutoff, cutoff)):
            issues.append({"type": "Inactive user", "record": f"{u['full_name']} ({u['email']})",
                           "detail": "No login for 30+ days"})
    return issues


@route("GET", "/admin/audit-log", roles=ADMIN)
def audit_log(ctx):
    hid = ctx.params.get("hospital_id")
    with read() as c:
        return c.all("""SELECT l.log_id, h.hospital_name, c.resource_type, l.old_total, l.new_total,
                               l.old_occupied, l.new_occupied, l.reason, l.flagged, l.reviewed, l.review_action,
                               u.full_name AS changed_by, l.changed_at
                        FROM capacity_audit_log l
                        JOIN capacity c ON c.capacity_id = l.capacity_id
                        JOIN hospitals h ON h.hospital_id = c.hospital_id
                        LEFT JOIN users u ON u.user_id = l.changed_by
                        WHERE (? IS NULL OR h.hospital_id = ?)
                        ORDER BY l.changed_at DESC, l.log_id DESC LIMIT 150""", (hid, hid))


@route("GET", "/admin/login-log", roles=ADMIN)
def login_log(ctx):
    with read() as c:
        return c.all("SELECT email, success, reason, ip, created_at FROM login_log "
                     "ORDER BY created_at DESC, log_id DESC LIMIT 150")


@route("POST", "/admin/reset-demo", roles=ADMIN)
def reset_demo(ctx):
    """Wipe everything and load the demo data again (the demo accounts return to Demo@1234)."""
    db.reset_database()
    return {"ok": True}

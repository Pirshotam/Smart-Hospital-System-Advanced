"""Registration, login (with lockout) and account endpoints."""
import secrets
from datetime import timedelta

from hospital_core import validate as v
from hospital_core.config import LOCK_MINUTES, MAX_FAILED_LOGINS
from hospital_core.db import parse, read, ts, tx, utc_now
from hospital_core.router import ApiError, route
from hospital_core.security import dummy_hash, hash_password, password_problem, verify_password
from hospital_core.services.notifier import notify_admins


def public_user(u):
    return {"user_id": u["user_id"], "full_name": u["full_name"], "email": u["email"],
            "role": u["role"], "hospital_id": u["hospital_id"], "status": u["status"]}


def _log(c, email, user_id, success, reason, ip):
    c.run("INSERT INTO login_log (email, user_id, success, reason, ip, created_at) VALUES (?,?,?,?,?,?)",
          (email[:255], user_id, 1 if success else 0, reason, ip, ts(utc_now())))


def _password(b, key="password"):
    pw = b.get(key) or ""
    if len(pw) > 128:
        raise ApiError(422, "Password is too long")
    problem = password_problem(pw)
    if problem:
        raise ApiError(422, problem)
    return pw


@route("GET", "/auth/hospitals", public=True)
def hospitals_for_registration(ctx):
    """Public list so new hospital staff can pick the hospital they work at."""
    with read() as c:
        return c.all("SELECT hospital_id, hospital_name, area FROM hospitals "
                     "WHERE account_status <> 'suspended' AND verification_status <> 'rejected' "
                     "ORDER BY hospital_name")


@route("POST", "/auth/register", public=True)
def register(ctx):
    b = ctx.body
    role = v.choice(b, "role", ("patient", "coordinator", "hospital_staff"))
    full_name = v.text(b, "full_name", 2, 150)
    email = v.email(b.get("email"))
    password = _password(b)
    phone = v.text(b, "phone", 0, 30, required=False)
    nh = b.get("new_hospital")
    with tx() as c:
        if c.one("SELECT 1 AS x FROM users WHERE email = ?", (email,)):
            raise ApiError(409, "An account with this email already exists")
        hospital_id = None
        if role == "hospital_staff":
            if nh:
                hospital_id = c.insert(
                    """INSERT INTO hospitals (hospital_name, area, city, location, latitude, longitude, contact,
                           created_at) VALUES (?,?,?,?,?,?,?,?)""",
                    (v.text(nh, "hospital_name", 3, 200), v.text(nh, "area", 2, 100),
                     v.text(nh, "city", 0, 100, required=False, default="Hyderabad"),
                     v.text(nh, "location", 3, 255), v.number(nh, "latitude", -90, 90),
                     v.number(nh, "longitude", -180, 180), v.text(nh, "contact", 0, 50, required=False),
                     ts(utc_now())))
            elif b.get("hospital_id"):
                row = c.one("SELECT hospital_id FROM hospitals WHERE hospital_id = ? "
                            "AND account_status <> 'suspended'", (b["hospital_id"],))
                if not row:
                    raise ApiError(404, "Hospital not found")
                hospital_id = row["hospital_id"]
            else:
                raise ApiError(422, "Choose your hospital or register a new one")
        # patients can use the system immediately; every other role needs admin approval
        status = "active" if role == "patient" else "pending"
        c.run("""INSERT INTO users (full_name, email, phone, password_hash, role, hospital_id, status, created_at)
                 VALUES (?,?,?,?,?,?,?,?)""",
              (full_name, email, phone, hash_password(password), role, hospital_id, status, ts(utc_now())))
        if status == "pending":
            notify_admins(c, "Approval needed", f"New {role.replace('_', ' ')} account: {full_name} ({email}).")
    return {"status": status,
            "message": ("Account created. You can log in now." if status == "active" else
                        "Account created. An administrator must approve it before you can log in.")}


@route("POST", "/auth/login", public=True)
def login(ctx):
    email = (ctx.body.get("email") or "").strip().lower()
    password = (ctx.body.get("password") or "")[:128]
    error, user = None, None
    with tx() as c:
        u = c.one("SELECT * FROM users WHERE email = ?", (email,))
        now = utc_now()
        if u and u["locked_until"] and parse(u["locked_until"]) > now:
            error = (429, f"Too many failed attempts. Try again in {LOCK_MINUTES} minutes.")
            _log(c, email, u["user_id"], False, "locked", ctx.ip)
        elif not u or not verify_password(password, u["password_hash"] if u else dummy_hash()):
            if u:
                fails = u["failed_logins"] + 1
                locked = ts(now + timedelta(minutes=LOCK_MINUTES)) if fails >= MAX_FAILED_LOGINS else None
                c.run("UPDATE users SET failed_logins = ?, locked_until = ? WHERE user_id = ?",
                      (0 if locked else fails, locked, u["user_id"]))
            _log(c, email, u["user_id"] if u else None, False, "bad_credentials", ctx.ip)
            error = (401, "Incorrect email or password")
        elif u["status"] == "pending":
            _log(c, email, u["user_id"], False, "pending_approval", ctx.ip)
            error = (403, "Your account is waiting for administrator approval.")
        elif u["status"] == "suspended":
            _log(c, email, u["user_id"], False, "suspended", ctx.ip)
            error = (403, "This account has been suspended. Contact an administrator.")
        else:
            c.run("UPDATE users SET failed_logins = 0, locked_until = NULL, last_login = ? WHERE user_id = ?",
                  (ts(now), u["user_id"]))
            _log(c, email, u["user_id"], True, "ok", ctx.ip)
            user = u
    if error:   # raised after the transaction so the failed-attempt counter is saved
        raise ApiError(*error)
    return {"access_token": secrets.token_urlsafe(16), "token_type": "session", "user": public_user(user)}


@route("GET", "/auth/me")
def me(ctx):
    out = public_user(ctx.user)
    if ctx.user["hospital_id"]:
        with read() as c:
            out["hospital"] = c.one("SELECT hospital_id, hospital_name, verification_status, account_status "
                                    "FROM hospitals WHERE hospital_id = ?", (ctx.user["hospital_id"],))
    return out


@route("POST", "/auth/change-password")
def change_password(ctx):
    new = _password(ctx.body, "new_password")
    with tx() as c:
        row = c.one("SELECT password_hash FROM users WHERE user_id = ?", (ctx.user["user_id"],))
        if not verify_password(ctx.body.get("old_password") or "", row["password_hash"]):
            raise ApiError(403, "Current password is incorrect")
        c.run("UPDATE users SET password_hash = ? WHERE user_id = ?", (hash_password(new), ctx.user["user_id"]))
    return {"message": "Password changed"}

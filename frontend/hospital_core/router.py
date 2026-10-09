"""A tiny in-process 'API': maps (method, path) to a handler and enforces who may call it.

The Streamlit pages call api.get/post/... exactly as they called the FastAPI server before; the
request is dispatched here, inside the same Python process. Every handler is protected by the
role list given to @route, so access control lives in the service layer, not in the pages."""
import re

from hospital_core import db, maintenance


class ApiError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


class Ctx:
    def __init__(self, user, body, params, path, ip=None):
        self.user, self.body, self.params, self.path, self.ip = user, body or {}, params or {}, path, ip

    def pid(self, name):
        try:
            return int(self.path[name])
        except (KeyError, ValueError):
            raise ApiError(404, "Not found")


ROUTES = []


def route(method, pattern, roles=None, public=False):
    rx = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn, roles, public))
        return fn
    return deco


def load_user(user_id):
    """Fresh copy of the logged-in user - a suspended account loses access immediately."""
    if not user_id:
        raise ApiError(401, "Not logged in")
    with db.read() as c:
        u = c.one("SELECT user_id, full_name, email, phone, role, hospital_id, status FROM users "
                  "WHERE user_id = ?", (user_id,))
    if not u or u["status"] != "active":
        raise ApiError(401, "Account is not active")
    return u


def dispatch(method, path, user_id=None, body=None, params=None, ip=None):
    db.ensure_ready()
    maintenance.run()
    for m, rx, fn, roles, public in ROUTES:
        match = rx.match(path) if m == method else None
        if not match:
            continue
        user = None
        if not public:
            user = load_user(user_id)
            if roles and user["role"] not in roles:
                raise ApiError(403, "You do not have permission to do this")
        return fn(Ctx(user, body, params, match.groupdict(), ip))
    raise ApiError(404, f"Unknown request: {method} {path}")


def staff(ctx):
    """Hospital id of the logged-in staff member."""
    if not ctx.user["hospital_id"]:
        raise ApiError(403, "Your account is not linked to a hospital")
    return ctx.user["hospital_id"]


def ensure_hospital_verified(c, hospital_id):
    h = c.one("SELECT verification_status, account_status FROM hospitals WHERE hospital_id = ?", (hospital_id,))
    if not h:
        raise ApiError(404, "Hospital not found")
    if h["verification_status"] != "verified":
        raise ApiError(403, "Your hospital has not been verified by an administrator yet")
    if h["account_status"] != "active":
        raise ApiError(403, "Your hospital account is not active")

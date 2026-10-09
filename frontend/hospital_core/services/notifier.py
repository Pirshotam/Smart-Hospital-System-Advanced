"""In-app notifications. To add real SMS/push, send it inside notify() (Twilio, Firebase, ...)."""
from hospital_core.db import ts, utc_now


def notify(c, user_id, title, message, kind="info", request_id=None):
    c.run("INSERT INTO notifications (user_id, title, message, kind, request_id, created_at) "
          "VALUES (?,?,?,?,?,?)", (user_id, title[:150], message[:500], kind, request_id, ts(utc_now())))


def notify_admins(c, title, message, kind="admin"):
    for r in c.all("SELECT user_id FROM users WHERE role = 'admin' AND status = 'active'"):
        notify(c, r["user_id"], title, message, kind)


def notify_hospital_staff(c, hospital_id, title, message, kind="referral", request_id=None):
    for r in c.all("SELECT user_id FROM users WHERE role = 'hospital_staff' AND hospital_id = ? "
                   "AND status = 'active'", (hospital_id,)):
        notify(c, r["user_id"], title, message, kind, request_id)

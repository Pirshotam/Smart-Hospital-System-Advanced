from hospital_core.db import read, tx
from hospital_core.router import route


@route("GET", "/notifications")
def my_notifications(ctx):
    with read() as c:
        items = c.all("SELECT notification_id, title, message, kind, request_id, is_read, created_at "
                      "FROM notifications WHERE user_id = ? ORDER BY created_at DESC, notification_id DESC "
                      "LIMIT 30", (ctx.user["user_id"],))
        unread = c.one("SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND is_read = 0",
                       (ctx.user["user_id"],))["n"]
    return {"unread": unread, "items": items}


@route("POST", "/notifications/read-all")
def read_all(ctx):
    with tx() as c:
        c.run("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (ctx.user["user_id"],))
    return {"ok": True}

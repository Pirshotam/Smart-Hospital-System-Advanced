"""Housekeeping that used to be background jobs. Streamlit has no always-on worker, so it runs
automatically (at most every few seconds) whenever anyone uses the app:
  * release bed holds whose 15 minutes ran out (and expire those requests)
  * store an hourly capacity snapshot (feeds trends and forecasting)"""
import threading
import time

from hospital_core import db
from hospital_core.config import SNAPSHOT_INTERVAL_MIN
from hospital_core.db import ts, utc_now
from hospital_core.services import reservations

_lock = threading.Lock()
_last = 0.0


def run(force=False):
    global _last
    if not force and time.time() - _last < 5:
        return
    if not _lock.acquire(blocking=False):
        return
    try:
        _last = time.time()
        with db.read() as c:
            due = c.one("SELECT 1 AS x FROM reservations WHERE state = 'active' AND expires_at < ? LIMIT 1",
                        (ts(utc_now()),))
            last_snap = c.one("SELECT MAX(captured_at) AS t FROM capacity_snapshots")["t"]
        snapshot_due = last_snap is None or db.minutes_between(last_snap) >= SNAPSHOT_INTERVAL_MIN - 5
        if due or snapshot_due:
            with db.tx() as c:
                if due:
                    reservations.expire_stale(c)
                if snapshot_due:
                    c.run("""INSERT INTO capacity_snapshots (hospital_id, resource_type, occupied,
                                 total_capacity, captured_at)
                             SELECT hospital_id, resource_type, occupied, total_capacity, ? FROM capacity""",
                          (ts(utc_now()),))
    finally:
        _lock.release()

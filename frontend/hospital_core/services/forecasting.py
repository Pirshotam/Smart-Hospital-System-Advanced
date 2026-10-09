"""Capacity prediction, capacity forecasting, demand forecasting and wait-time prediction.
Pure-Python statistics on top of the hourly capacity_snapshots and the referral history."""
import math
from datetime import timedelta

from hospital_core.config import LOCAL_TZ_HOURS
from hospital_core.db import parse, ts, utc_now

FORECAST_RESOURCES = ("icu_bed", "emergency_bed", "general_bed")


# ---------------------------------------------------------------- pure helpers
def local_hour(dt):
    return (dt.hour + LOCAL_TZ_HOURS) % 24


def limited_threshold(total):
    """Capacity counts as 'limited' at 10% free (minimum 1 bed)."""
    return max(1, math.ceil(0.10 * total))


def linear_slope(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return 0.0 if den == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def trend_slope(series, now, window_hours=24):
    """Occupied beds gained per hour over the last window. series = [(time, occupied, total)]."""
    pts = [((t - now).total_seconds() / 3600, occ) for t, occ, _ in series
           if (now - t).total_seconds() <= window_hours * 3600]
    return linear_slope([p[0] for p in pts], [p[1] for p in pts])


def predict_limited(series, now, available, total):
    """Capacity prediction: when may this resource become limited?"""
    thr = limited_threshold(total)
    slope = trend_slope(series, now)
    if available <= thr:
        return {"status": "limited_now", "hours": 0.0, "slope_per_hour": round(slope, 2), "threshold": thr}
    if slope > 0.02:
        hours = (available - thr) / slope
        if hours <= 72:
            return {"status": "limited_soon", "hours": round(hours, 1),
                    "slope_per_hour": round(slope, 2), "threshold": thr}
    status = "improving" if slope < -0.02 else "stable"
    return {"status": status, "hours": None, "slope_per_hour": round(slope, 2), "threshold": thr}


def hourly_profile(series):
    buckets = {}
    for t, occ, _ in series:
        buckets.setdefault(local_hour(t), []).append(occ)
    return {h: sum(v) / len(v) for h, v in buckets.items()}


def forecast_next(series, now, current_occ, total, horizon=24):
    """Capacity forecasting: expected occupancy for the next `horizon` hours, applying the typical
    daily pattern (hour-of-day profile) to the current level."""
    profile = hourly_profile(series)
    base = profile.get(local_hour(now), current_occ) if profile else current_occ
    out = []
    for k in range(1, horizon + 1):
        t = now + timedelta(hours=k)
        delta = profile.get(local_hour(t), base) - base if profile else 0
        occ = int(round(min(max(current_occ + delta, 0), total)))
        out.append({"hours_ahead": k, "hour_local": local_hour(t), "time": t.isoformat(),
                    "occupied": occ, "available": total - occ,
                    "occupancy_pct": round(100 * occ / total, 1) if total else 0.0})
    return out


def estimate_wait_min(avg_response, queue, hospital_id, occupancy_ratio):
    """Hospital wait-time prediction (minutes until the hospital responds/admits)."""
    base = avg_response.get(hospital_id, 12.0)
    q = queue.get(hospital_id, 0)
    return int(round(base * (1 + 0.5 * q) * (1 + max(0.0, occupancy_ratio - 0.7))))


# ---------------------------------------------------------------- database wrappers
def wait_inputs(c):
    avg = {r["hid"]: float(r["avg_min"]) for r in c.all(
        """SELECT selected_hospital AS hid,
                  AVG((julianday(responded_at) - julianday(created_at)) * 1440) AS avg_min
           FROM referral_requests WHERE responded_at IS NOT NULL AND selected_hospital IS NOT NULL
           GROUP BY selected_hospital""")}
    queue = {r["hid"]: int(r["n"]) for r in c.all(
        """SELECT selected_hospital AS hid, COUNT(*) AS n FROM referral_requests
           WHERE request_status IN ('request_sent','hospital_reviewing') AND selected_hospital IS NOT NULL
           GROUP BY selected_hospital""")}
    return avg, queue


def capacity_outlook(c, hospital_id, horizon=24):
    """Prediction + 24h forecast for ICU, emergency and general beds of one hospital."""
    now = utc_now()
    caps = {r["resource_type"]: r for r in c.all(
        """SELECT resource_type, total_capacity, occupied, available FROM capacity
           WHERE hospital_id = ? AND resource_type IN ('icu_bed','emergency_bed','general_bed')""",
        (hospital_id,))}
    series = {}
    for r in c.all("""SELECT resource_type, captured_at, occupied, total_capacity FROM capacity_snapshots
                      WHERE hospital_id = ? AND captured_at >= ? ORDER BY captured_at""",
                   (hospital_id, ts(now - timedelta(days=14)))):
        series.setdefault(r["resource_type"], []).append(
            (parse(r["captured_at"]), r["occupied"], r["total_capacity"]))
    out = {}
    for res, cap in caps.items():
        s = series.get(res, [])
        out[res] = {"current": {"total": cap["total_capacity"], "occupied": cap["occupied"],
                                "available": cap["available"]},
                    "prediction": predict_limited(s, now, cap["available"], cap["total_capacity"]),
                    "forecast": forecast_next(s, now, cap["occupied"], cap["total_capacity"], horizon)}
    return out


def daily_trends(c, days=14, hospital_id=None):
    """Capacity trends by date (average occupancy %, per resource)."""
    rows = c.all("""SELECT DATE(captured_at) AS day, resource_type,
                           ROUND(100.0 * SUM(occupied) / NULLIF(SUM(total_capacity), 0), 1) AS occupancy_pct
                    FROM capacity_snapshots
                    WHERE captured_at >= ? AND resource_type IN ('icu_bed','emergency_bed','general_bed')
                      AND (? IS NULL OR hospital_id = ?)
                    GROUP BY day, resource_type ORDER BY day""",
                 (ts(utc_now() - timedelta(days=days)), hospital_id, hospital_id))
    return [{"day": str(r["day"]), "resource_type": r["resource_type"],
             "occupancy_pct": float(r["occupancy_pct"] or 0)} for r in rows]


def demand_overview(c):
    """Demand forecasting: busy hours, busy services, peak emergency periods, next-24h forecast."""
    since = ts(utc_now() - timedelta(days=28))
    days = max(1, int(c.one("SELECT COUNT(DISTINCT DATE(created_at)) AS d FROM referral_requests "
                            "WHERE created_at >= ?", (since,))["d"] or 1))
    total, urgent = [0] * 24, [0] * 24
    for r in c.all("""SELECT CAST(strftime('%H', created_at) AS INTEGER) AS h, COUNT(*) AS n,
                             SUM(urgency IN ('high','critical')) AS urgent
                      FROM referral_requests WHERE created_at >= ? GROUP BY h""", (since,)):
        lh = (int(r["h"]) + LOCAL_TZ_HOURS) % 24
        total[lh] += int(r["n"])
        urgent[lh] += int(r["urgent"] or 0)
    by_hour = [{"hour": h, "label": f"{h:02d}:00", "avg_requests": round(total[h] / days, 2),
                "avg_emergency": round(urgent[h] / days, 2)} for h in range(24)]
    top3 = sorted(range(24), key=lambda h: urgent[h], reverse=True)[:3]
    peaks = [f"{h:02d}:00-{(h + 1) % 24:02d}:00" for h in sorted(top3) if urgent[h] > 0]

    names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]       # strftime('%w'): 0 = Sunday
    by_day = [{"day": names[int(r["d"])], "requests": int(r["n"])} for r in c.all(
        """SELECT CAST(strftime('%w', created_at) AS INTEGER) AS d, COUNT(*) AS n FROM referral_requests
           WHERE created_at >= ? GROUP BY d ORDER BY d""", (since,))]
    services = [{"name": r["name"], "requests": int(r["n"])} for r in c.all(
        """SELECT s.service_name AS name, COUNT(*) AS n FROM referral_requests r
           JOIN services s ON s.service_id = r.required_service WHERE r.created_at >= ?
           GROUP BY s.service_name ORDER BY n DESC""", (since,))]
    resources = [{"name": r["name"], "requests": int(r["n"])} for r in c.all(
        """SELECT required_resource AS name, COUNT(*) AS n FROM referral_requests WHERE created_at >= ?
           GROUP BY required_resource ORDER BY n DESC""", (since,))]
    now = utc_now()
    nxt = []
    for k in range(1, 25):
        lh = local_hour(now + timedelta(hours=k))
        nxt.append({"hours_ahead": k, "hour_local": lh, "label": f"{lh:02d}:00",
                    "expected_requests": by_hour[lh]["avg_requests"]})
    return {"days_of_data": days, "by_hour": by_hour, "peak_emergency_periods": peaks,
            "by_weekday": by_day, "top_services": services, "top_resources": resources, "next_24h": nxt}

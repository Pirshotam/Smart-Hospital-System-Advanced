"""System-level analytics for administrators (the heatmap is also open to coordinators)."""
from hospital_core.config import BED_TYPES
from hospital_core.db import read
from hospital_core.router import route
from hospital_core.services import forecasting

BEDS_SQL = "(" + ",".join(f"'{b}'" for b in BED_TYPES) + ")"


@route("GET", "/analytics/overview", roles=("admin",))
def overview(ctx):
    with read() as c:
        highest = c.all(f"""SELECT h.hospital_id, h.hospital_name, h.area, SUM(c.occupied) AS occupied,
                                   SUM(c.total_capacity) AS total,
                                   ROUND(100.0 * SUM(c.occupied) / NULLIF(SUM(c.total_capacity), 0), 1) AS occupancy_pct
                            FROM hospitals h JOIN capacity c ON c.hospital_id = h.hospital_id
                            WHERE c.resource_type IN {BEDS_SQL} AND h.verification_status = 'verified'
                            GROUP BY h.hospital_id, h.hospital_name, h.area
                            ORDER BY occupancy_pct DESC LIMIT 10""")
        areas = c.all(f"""SELECT h.area, COUNT(DISTINCT h.hospital_id) AS hospitals,
                                 SUM(c.available) AS available_beds, SUM(c.total_capacity) AS total_beds,
                                 ROUND(100.0 * SUM(c.available) / NULLIF(SUM(c.total_capacity), 0), 1) AS free_pct
                          FROM hospitals h JOIN capacity c ON c.hospital_id = h.hospital_id
                          WHERE c.resource_type IN {BEDS_SQL} AND h.verification_status = 'verified'
                          GROUP BY h.area ORDER BY free_pct ASC""")
        response = c.all("""SELECT h.hospital_name,
                                   ROUND(AVG((julianday(r.responded_at) - julianday(r.created_at)) * 1440), 1)
                                       AS avg_response_min, COUNT(*) AS requests
                            FROM referral_requests r JOIN hospitals h ON h.hospital_id = r.selected_hospital
                            WHERE r.responded_at IS NOT NULL
                            GROUP BY h.hospital_id, h.hospital_name ORDER BY avg_response_min""")
        by_status = c.all("SELECT request_status, COUNT(*) AS n FROM referral_requests GROUP BY request_status")
        avg_all = c.one("""SELECT ROUND(AVG((julianday(responded_at) - julianday(created_at)) * 1440), 1) AS m
                           FROM referral_requests WHERE responded_at IS NOT NULL""")["m"]
        users = c.all("SELECT role, status, COUNT(*) AS n FROM users GROUP BY role, status")
        hospitals = c.all("SELECT verification_status, COUNT(*) AS n FROM hospitals GROUP BY verification_status")
        beds = c.one(f"""SELECT SUM(total_capacity) AS total, SUM(occupied) AS occupied, SUM(available) AS available
                         FROM capacity WHERE resource_type IN {BEDS_SQL}""")
    return {"highest_occupancy": highest, "areas_low_capacity": areas, "response_by_hospital": response,
            "requests_by_status": by_status, "avg_response_min": avg_all, "users": users,
            "hospitals": hospitals, "beds": beds}


@route("GET", "/analytics/trends", roles=("admin",))
def trends(ctx):
    days = min(max(int(ctx.params.get("days", 14)), 1), 60)
    hid = ctx.params.get("hospital_id")
    with read() as c:
        return forecasting.daily_trends(c, days, int(hid) if hid else None)


@route("GET", "/analytics/demand", roles=("admin",))
def demand(ctx):
    with read() as c:
        return forecasting.demand_overview(c)


@route("GET", "/analytics/outlook/{hospital_id}", roles=("admin",))
def outlook(ctx):
    with read() as c:
        return forecasting.capacity_outlook(c, ctx.pid("hospital_id"))


@route("GET", "/analytics/heatmap", roles=("admin", "coordinator"))
def heatmap(ctx):
    """Regional capacity: occupancy per hospital (for the heatmap)."""
    with read() as c:
        points = c.all(f"""SELECT h.hospital_id, h.hospital_name, h.area, h.latitude, h.longitude,
                                  SUM(c.occupied) AS occupied, SUM(c.total_capacity) AS total,
                                  SUM(c.available) AS available,
                                  ROUND(100.0 * SUM(c.occupied) / NULLIF(SUM(c.total_capacity), 0), 1) AS occupancy_pct
                           FROM hospitals h JOIN capacity c ON c.hospital_id = h.hospital_id
                           WHERE c.resource_type IN {BEDS_SQL} AND h.verification_status = 'verified'
                             AND h.account_status = 'active'
                           GROUP BY h.hospital_id, h.hospital_name, h.area, h.latitude, h.longitude""")
    return [{**p, "occupancy_pct": float(p["occupancy_pct"] or 0), "available": int(p["available"] or 0),
             "occupied": int(p["occupied"] or 0), "total": int(p["total"] or 0)} for p in points]

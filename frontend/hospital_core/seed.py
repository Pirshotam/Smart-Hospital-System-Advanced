"""Demo data, loaded automatically the first time the app starts (or when an admin resets it).
All demo accounts use the password Demo@1234. Hospital names are fictional; coordinates are
approximate points around Hyderabad / Jamshoro."""
import math
import random
from datetime import timedelta

from hospital_core.config import DEMO_PASSWORD, LOCAL_TZ_HOURS
from hospital_core.db import ts, utc_now
from hospital_core.security import hash_password

# cap = {resource: (total, occupied)}; age = minutes since the last capacity update
HOSPITALS = [
    dict(name="Indus City General Hospital", area="Latifabad", loc="Latifabad, Hyderabad", lat=25.3797, lon=68.3547,
         verified=True, age=4, services=["Cardiology", "Neurology", "Orthopedics", "Dialysis"],
         cap={"general_bed": (120, 95), "emergency_bed": (20, 14), "icu_bed": (20, 17), "nicu_bed": (10, 6),
              "ventilator": (12, 9), "operation_theatre": (6, 2), "isolation_bed": (10, 4),
              "dialysis": (8, 5), "trauma": (6, 3), "ambulance": (5, 2)}),
    dict(name="Sindh Care Medical Center", area="Qasimabad", loc="Qasimabad, Hyderabad", lat=25.4167, lon=68.3333,
         verified=True, age=10, services=["Cardiology", "Orthopedics"],
         cap={"general_bed": (80, 60), "emergency_bed": (15, 9), "icu_bed": (12, 12), "ventilator": (8, 8),
              "operation_theatre": (4, 1), "trauma": (4, 1), "ambulance": (4, 3)}),          # ICU full
    dict(name="Jamshoro Teaching Hospital", area="Jamshoro", loc="Jamshoro", lat=25.4290, lon=68.2810,
         verified=True, age=6, services=["Cardiology", "Neurology", "Pediatrics", "Obstetrics", "Dialysis"],
         cap={"general_bed": (200, 170), "emergency_bed": (30, 22), "icu_bed": (25, 20), "nicu_bed": (15, 11),
              "ventilator": (15, 10), "operation_theatre": (8, 4), "isolation_bed": (15, 9),
              "dialysis": (10, 6), "trauma": (10, 5), "ambulance": (8, 4)}),
    dict(name="Hirabad Heart & Trauma Institute", area="Hirabad", loc="Hirabad, Hyderabad", lat=25.3920, lon=68.3700,
         verified=True, age=3, services=["Cardiology", "Orthopedics", "Neurology"],
         cap={"emergency_bed": (18, 10), "icu_bed": (16, 9), "ventilator": (10, 6),
              "operation_theatre": (5, 2), "trauma": (12, 6), "ambulance": (4, 1)}),
    dict(name="Saddar Community Hospital", area="Saddar", loc="Saddar, Hyderabad", lat=25.3850, lon=68.3650,
         verified=True, age=25, services=["Pediatrics"],                                      # no ventilators
         cap={"general_bed": (50, 44), "emergency_bed": (8, 6), "icu_bed": (4, 3), "ambulance": (2, 1)}),
    dict(name="Kotri District Hospital", area="Kotri", loc="Kotri", lat=25.3660, lon=68.3080,
         verified=True, age=180, services=["Pediatrics", "Obstetrics"],                       # stale data
         cap={"general_bed": (90, 70), "emergency_bed": (12, 8), "icu_bed": (8, 7),
              "ventilator": (4, 3), "nicu_bed": (6, 2)}),
    dict(name="Al-Noor Women & Children Hospital", area="Qasimabad", loc="Hyderabad", lat=25.4000, lon=68.3450,
         verified=True, age=8, services=["Pediatrics", "Obstetrics"],
         cap={"general_bed": (60, 40), "nicu_bed": (12, 7), "emergency_bed": (10, 4),
              "icu_bed": (6, 2), "ventilator": (5, 2)}),
    dict(name="New Horizon Clinic", area="Hirabad", loc="Hyderabad", lat=25.4100, lon=68.3700,
         verified=False, age=15, services=["Orthopedics"],                                    # pending verification
         cap={"general_bed": (30, 10), "emergency_bed": (5, 2), "icu_bed": (2, 0)}),
]
SERVICES = ["Cardiology", "Neurology", "Pediatrics", "Orthopedics", "Dialysis", "Obstetrics"]
RESOURCE_SERVICE = {"icu_bed": ["Cardiology", "Neurology", None], "nicu_bed": ["Pediatrics"],
                    "trauma": ["Orthopedics", "Neurology"], "dialysis": ["Dialysis"],
                    "emergency_bed": ["Cardiology", "Neurology", None], "general_bed": [None, "Pediatrics"]}
PATHS = {
    "admitted": ["searching", "request_sent", "hospital_reviewing", "accepted", "patient_transferred", "admitted"],
    "rejected": ["searching", "request_sent", "hospital_reviewing", "rejected"],
    "expired": ["searching", "request_sent", "expired"],
    "cancelled": ["searching", "request_sent", "cancelled"],
}
CASES = [
    "65 year old male, chest pain for 2 hours, BP 90/60, HR 120, SpO2 88%. History of diabetes and hypertension.",
    "Road traffic accident, 28 year old male, head injury and bleeding, unconscious at the scene.",
    "Newborn, 3 days old, premature, difficulty breathing, needs incubator.",
    "45 year old female with kidney failure, creatinine very high, needs dialysis today.",
    "7 year old child with high fever and seizure, SpO2 94%.",
]


def run(c):
    """Fill an empty database. `c` is a hospital_core.db.Conn inside a write transaction."""
    random.seed(42)
    now = utc_now()

    hids = []
    for h in HOSPITALS:
        hids.append(c.insert(
            """INSERT INTO hospitals (hospital_name, area, city, location, latitude, longitude, contact,
                   verification_status, verified_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (h["name"], h["area"], "Hyderabad", h["loc"], h["lat"], h["lon"], "+92-22-0000000",
             "verified" if h["verified"] else "pending", ts(now) if h["verified"] else None, ts(now))))

    pw = hash_password(DEMO_PASSWORD)          # one hash is reused for every demo account

    def add_user(name, email, role, status="active", hospital_id=None):
        return c.insert(
            """INSERT INTO users (full_name, email, password_hash, role, hospital_id, status, approved_at, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (name, email, pw, role, hospital_id, status, ts(now) if status == "active" else None, ts(now)))

    admin_id = add_user("Demo Admin", "admin@demo.local", "admin")
    coord_id = add_user("Demo Coordinator", "coordinator@demo.local", "coordinator")
    add_user("Demo Patient", "patient@demo.local", "patient")
    add_user("New Coordinator (pending)", "newcoord@demo.local", "coordinator", "pending")
    staff = {}
    for i, (h, hid) in enumerate(zip(HOSPITALS, hids), 1):
        staff[hid] = add_user(f"Staff {i}", f"staff{i}@demo.local", "hospital_staff",
                              "active" if h["verified"] else "pending", hid)

    sid = {s: c.insert("INSERT INTO services (service_name) VALUES (?)", (s,)) for s in SERVICES}
    for h, hid in zip(HOSPITALS, hids):
        for s in h["services"]:
            c.run("INSERT INTO hospital_services (hospital_id, service_id) VALUES (?,?)", (hid, sid[s]))

    # capacity + 14 days of hourly snapshots with a daily rhythm (peak ~20:00 local)
    cap_ids, snaps = {}, []
    for h, hid in zip(HOSPITALS, hids):
        for res, (total, occ) in h["cap"].items():
            cap_ids[(hid, res)] = c.insert(
                """INSERT INTO capacity (hospital_id, resource_type, total_capacity, occupied, last_updated)
                   VALUES (?,?,?,?,?)""", (hid, res, total, occ, ts(now - timedelta(minutes=h["age"]))))
            if res in ("icu_bed", "emergency_bed", "general_bed"):
                amp = max(1, round(total * 0.08))
                base_h = (now.hour + LOCAL_TZ_HOURS) % 24
                for back in range(14 * 24, 0, -1):
                    t = now - timedelta(hours=back)
                    lh = (t.hour + LOCAL_TZ_HOURS) % 24
                    value = (occ + amp * math.sin((lh - 14) / 24 * 2 * math.pi)
                             - amp * math.sin((base_h - 14) / 24 * 2 * math.pi) + random.uniform(-1, 1))
                    snaps.append((hid, res, int(round(min(max(value, 0), total))), total, ts(t)))
    c.many("INSERT INTO capacity_snapshots (hospital_id, resource_type, occupied, total_capacity, captured_at) "
           "VALUES (?,?,?,?,?)", snaps)

    # 28 days of past referrals; busier 17:00-23:00 local so demand forecasting has a pattern
    verified = [hid for h, hid in zip(HOSPITALS, hids) if h["verified"]]
    hour_weights = [1 + 5 * (17 <= (h + LOCAL_TZ_HOURS) % 24 <= 23) for h in range(24)]
    outcomes = ["admitted"] * 10 + ["rejected"] * 4 + ["expired"] * 3 + ["cancelled"] * 2
    for i in range(160):
        hid = random.choice(verified)
        res = random.choice(list(RESOURCE_SERVICE))
        svc = random.choice(RESOURCE_SERVICE[res])
        outcome = random.choice(outcomes)
        urgency = random.choices(["low", "medium", "high", "critical"], [1, 4, 4, 2])[0]
        hour = random.choices(range(24), hour_weights)[0]
        created = (now - timedelta(days=random.randint(0, 27))).replace(
            hour=hour, minute=random.randint(0, 59), second=0, microsecond=0)
        if created + timedelta(minutes=90) > now:
            created -= timedelta(days=1)
        respond = random.randint(3, 28)
        responded = created + timedelta(minutes=respond) if outcome in ("admitted", "rejected") else None
        accepted = responded if outcome == "admitted" else None
        admitted = created + timedelta(minutes=respond + 20) if outcome == "admitted" else None
        discharged = (min(admitted + timedelta(days=random.randint(1, 5)), now)
                      if admitted and admitted + timedelta(days=1) < now and random.random() < 0.8 else None)
        rid = c.insert(
            """INSERT INTO referral_requests (request_code, patient_reference, created_by, required_resource,
                   needs_ventilator, required_service, urgency, selected_hospital, request_status, created_at,
                   responded_at, accepted_at, admitted_at, discharged_at, latitude, longitude)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"SEED{i:06d}", f"PT-{1000 + i}", coord_id, res, int(res == "icu_bed" and random.random() < 0.5),
             sid[svc] if svc else None, urgency, hid, outcome, ts(created), ts(responded), ts(accepted),
             ts(admitted), ts(discharged), 25.39, 68.36))
        for step, st in enumerate(PATHS[outcome]):
            c.run("INSERT INTO request_status_history (request_id, status, changed_by, changed_at) "
                  "VALUES (?,?,?,?)", (rid, st, coord_id, ts(created + timedelta(minutes=step * 4))))

    # patients that are currently admitted (hospital staff can discharge them)
    for hid in verified[:5]:
        for k in range(3):
            admitted = now - timedelta(hours=random.randint(2, 70))
            created = admitted - timedelta(minutes=40)
            rid = c.insert("""INSERT INTO referral_requests (request_code, patient_reference, created_by,
                                  required_resource, urgency, selected_hospital, request_status, case_summary,
                                  created_at, responded_at, accepted_at, admitted_at)
                              VALUES (?,?,?,'general_bed','medium',?,'admitted',?,?,?,?,?)""",
                           (f"ADM{hid:02d}{k:04d}", f"ADM-{hid}{k}", coord_id, hid,
                            CASES[(hid + k) % len(CASES)][:200], ts(created), ts(created + timedelta(minutes=10)),
                            ts(created + timedelta(minutes=10)), ts(admitted)))
            for step, st in enumerate(PATHS["admitted"]):
                c.run("INSERT INTO request_status_history (request_id, status, changed_by, changed_at) "
                      "VALUES (?,?,?,?)", (rid, st, coord_id, ts(created + timedelta(minutes=step * 8))))

    # one suspicious update waiting for admin review, plus a first notification
    c.run("""INSERT INTO capacity_audit_log (capacity_id, changed_by, old_total, new_total, old_occupied,
                 new_occupied, reason, flagged, changed_at) VALUES (?,?,25,25,5,20,'manual_update',1,?)""",
          (cap_ids[(hids[2], "icu_bed")], staff[hids[2]], ts(now)))
    c.run("INSERT INTO notifications (user_id, title, message, kind, created_at) VALUES (?,?,?,?,?)",
          (admin_id, "Approval needed", "Hospital staff and coordinator accounts are waiting for approval.",
           "admin", ts(now)))

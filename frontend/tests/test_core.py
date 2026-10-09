"""End-to-end tests of the whole app logic (no Streamlit needed).
Run from frontend/:   python -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest
from datetime import timedelta

os.environ["HOSPITAL_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hospital_core import db, handlers, maintenance, router   # noqa: E402,F401
from hospital_core.router import ApiError                       # noqa: E402
from hospital_core.services import ai_tools, forecasting, matcher   # noqa: E402

PW = "Demo@1234"
IDS = {}


def call(method, path, user=None, body=None, params=None):
    return router.dispatch(method, path, user_id=IDS.get(user), body=body, params=params)


def login(email):
    out = router.dispatch("POST", "/auth/login", body={"email": email, "password": PW})
    IDS[email] = out["user"]["user_id"]
    return out


def fail(status, *args, **kw):
    with unittest.TestCase().assertRaises(ApiError) as cm:
        call(*args, **kw)
    assert cm.exception.status == status, (cm.exception.status, cm.exception.detail)
    return cm.exception.detail


SEARCH = {"resource_type": "icu_bed", "needs_ventilator": True, "latitude": 25.3797, "longitude": 68.3547}
COORD, ADMIN, PATIENT, STAFF1 = "coordinator@demo.local", "admin@demo.local", "patient@demo.local", "staff1@demo.local"


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.ensure_ready()
        for e in (COORD, ADMIN, PATIENT, STAFF1, "staff3@demo.local"):
            login(e)

    # ---------------------------------------------------------------- security
    def test_01_login_rules(self):
        fail(401, "POST", "/auth/login", body={"email": COORD, "password": "wrong"})
        self.assertIn("approval", fail(403, "POST", "/auth/login", body={"email": "staff8@demo.local", "password": PW}))
        fail(401, "GET", "/hospital/capacity")                       # not logged in
        fail(403, "GET", "/hospital/capacity", user=COORD)           # wrong role
        fail(403, "GET", "/admin/users", user=PATIENT)
        fail(403, "POST", "/requests", user="staff1@demo.local", body={})

    def test_02_lockout(self):
        for _ in range(5):
            fail(401, "POST", "/auth/login", body={"email": "patient@demo.local", "password": "bad"})
        fail(429, "POST", "/auth/login", body={"email": "patient@demo.local", "password": PW})
        call("POST", "/admin/users/%d/reset-password" % IDS[PATIENT], ADMIN, {"new_password": PW})   # unlocks
        login(PATIENT)

    def test_03_registration_and_approval(self):
        r = call("POST", "/auth/register", body={"full_name": "Sara", "email": "sara@x.com", "password": "Strong123",
                                                 "role": "coordinator"})
        self.assertEqual(r["status"], "pending")
        fail(403, "POST", "/auth/login", body={"email": "sara@x.com", "password": "Strong123"})
        fail(422, "POST", "/auth/register", body={"full_name": "A", "email": "bad", "password": "x", "role": "patient"})
        fail(409, "POST", "/auth/register", body={"full_name": "Sara", "email": "sara@x.com", "password": "Strong123",
                                                  "role": "coordinator"})
        uid = next(u["user_id"] for u in call("GET", "/admin/users", ADMIN, params={"status": "pending"})
                   if u["email"] == "sara@x.com")
        call("PATCH", f"/admin/users/{uid}", ADMIN, {"status": "active"})
        self.assertEqual(login("sara@x.com") if False else router.dispatch(
            "POST", "/auth/login", body={"email": "sara@x.com", "password": "Strong123"})["user"]["role"], "coordinator")
        fail(400, "PATCH", f"/admin/users/{IDS[ADMIN]}", ADMIN, {"status": "suspended"})

    def test_04_new_hospital_must_be_verified(self):
        call("POST", "/auth/register", body={
            "full_name": "Dr New", "email": "new@h.com", "password": "Strong123", "role": "hospital_staff",
            "new_hospital": {"hospital_name": "Fresh Clinic", "area": "Latifabad", "location": "Road 1",
                             "latitude": 25.38, "longitude": 68.35}})
        users = {u["email"]: u for u in call("GET", "/admin/users", ADMIN)}
        call("PATCH", f"/admin/users/{users['new@h.com']['user_id']}", ADMIN, {"status": "active"})
        out = router.dispatch("POST", "/auth/login", body={"email": "new@h.com", "password": "Strong123"})
        IDS["new@h.com"] = out["user"]["user_id"]
        self.assertIn("not been verified", fail(403, "POST", "/hospital/capacity", "new@h.com",
                                                {"resource_type": "icu_bed", "total_capacity": 5}))
        hid = users["new@h.com"]["hospital_id"]
        hospitals = {h["hospital_id"]: h for h in call("GET", "/admin/hospitals", ADMIN)}
        self.assertEqual(hospitals[hid]["verification_status"], "pending")
        self.assertNotIn("Fresh Clinic", [h["hospital_name"] for h in call("POST", "/search", COORD, SEARCH)])
        call("POST", f"/admin/hospitals/{hid}/verify", ADMIN)
        call("POST", "/hospital/capacity", "new@h.com", {"resource_type": "icu_bed", "total_capacity": 5})

    # ---------------------------------------------------------------- search
    def test_05_search_and_reasons(self):
        res = call("POST", "/search", COORD, SEARCH)
        names = {h["hospital_name"]: h for h in res}
        self.assertTrue(res[0]["suitable"] and res[0]["is_best_match"])
        self.assertTrue(any(h["is_nearest"] for h in res))
        self.assertFalse(names["Sindh Care Medical Center"]["suitable"])         # ICU full
        self.assertIn("No ICU bed available", names["Sindh Care Medical Center"]["reasons"])
        self.assertIn("No ventilator available", names["Saddar Community Hospital"]["reasons"])
        self.assertNotIn("New Horizon Clinic", names)                            # unverified
        scores = [h["match_percent"] for h in res if h["suitable"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        only = call("POST", "/search", COORD, {**SEARCH, "include_unsuitable": False})
        self.assertTrue(all(h["suitable"] for h in only))
        near = call("POST", "/search", COORD, {**SEARCH, "max_distance_km": 3})
        self.assertTrue(all(h["distance_km"] <= 3 for h in near))
        fail(422, "POST", "/search", COORD, {"resource_type": "bogus", "latitude": 1, "longitude": 1})

    def test_06_compare_map_ai(self):
        ids = [h["hospital_id"] for h in call("POST", "/search", COORD, SEARCH) if h["suitable"]][:2]
        cmp_ = call("POST", "/compare", COORD, {"hospital_ids": ids, "latitude": 25.38, "longitude": 68.35})
        self.assertEqual(len(cmp_), 2)
        self.assertIn("outlook", cmp_[0])
        self.assertTrue(call("GET", "/map/hospitals", PATIENT))
        self.assertEqual(call("POST", "/ai/classify", PATIENT, {"text": "ICU bed with ventilator, unconscious"})["resource_type"], "icu_bed")
        self.assertIn("summary_text", call("POST", "/ai/summarize", PATIENT, {"text": "65 year old male chest pain"}))

    # ---------------------------------------------------------------- referral workflow
    def _icu(self, hospital_name):
        return next(c for c in call("GET", "/hospital/capacity", STAFF1) if c["resource_type"] == "icu_bed") \
            if hospital_name == "staff1" else None

    def test_07_full_referral_flow(self):
        before = {c["resource_type"]: c for c in call("GET", "/hospital/capacity", STAFF1)}
        req = call("POST", "/requests", COORD, {
            "patient_reference": "PT-TEST", "resource_type": "icu_bed", "needs_ventilator": True,
            "urgency": "critical", "latitude": 25.39, "longitude": 68.36,
            "case_text": "65 year old male, chest pain, BP 90/60, SpO2 88%"})
        rid = req["request_id"]
        self.assertIn("65 y/o", req["case_summary"])
        hid = next(h["hospital_id"] for h in call("POST", "/search", COORD, SEARCH) if h["hospital_name"].startswith("Indus"))
        out = call("POST", f"/requests/{rid}/send", COORD, {"hospital_id": hid})
        self.assertIn("reserved_until", out)
        mid = {c["resource_type"]: c for c in call("GET", "/hospital/capacity", STAFF1)}
        self.assertEqual(mid["icu_bed"]["reserved"], before["icu_bed"]["reserved"] + 1)
        self.assertEqual(mid["icu_bed"]["available"], before["icu_bed"]["available"] - 1)
        self.assertEqual(mid["ventilator"]["available"], before["ventilator"]["available"] - 1)   # ventilator held too
        # privacy: another hospital / the patient cannot touch it
        fail(403, "POST", f"/requests/{rid}/accept", "staff3@demo.local")
        fail(403, "GET", f"/requests/{rid}", "staff3@demo.local")
        fail(403, "GET", f"/requests/{rid}", PATIENT)
        self.assertNotIn("request_code", call("GET", f"/requests/{rid}", STAFF1)["request"])
        self.assertIn("request_code", call("GET", f"/requests/{rid}", COORD)["request"])
        fail(409, "POST", f"/requests/{rid}/transfer", COORD)                         # not accepted yet
        self.assertEqual(len(call("GET", "/hospital/requests", STAFF1)) >= 1, True)
        call("POST", f"/requests/{rid}/review", STAFF1)
        call("POST", f"/requests/{rid}/accept", STAFF1)
        info = call("POST", f"/requests/{rid}/dispatch", COORD, {"ambulance_unit": "AMB-1"})
        self.assertGreaterEqual(info["eta_min"], 1)
        t = call("GET", f"/requests/{rid}/tracking", COORD)
        self.assertTrue(t["dispatched"] and 0 <= t["progress"] <= 1)
        code = call("GET", f"/requests/{rid}", COORD)["request"]["request_code"]
        call("POST", "/requests/confirm-qr", STAFF1, {"code": "REFERRAL:" + code})
        after = {c["resource_type"]: c for c in call("GET", "/hospital/capacity", STAFF1)}
        self.assertEqual(after["icu_bed"]["occupied"], before["icu_bed"]["occupied"] + 1)
        self.assertEqual(after["icu_bed"]["reserved"], before["icu_bed"]["reserved"])
        call("POST", f"/requests/{rid}/admit", STAFF1)
        self.assertEqual(call("GET", f"/requests/{rid}", COORD)["request"]["request_status"], "admitted")
        self.assertIn("PT-TEST", [a["patient_reference"] for a in call("GET", "/hospital/admitted", STAFF1)])
        call("POST", f"/hospital/admitted/{rid}/discharge", STAFF1)
        end = {c["resource_type"]: c for c in call("GET", "/hospital/capacity", STAFF1)}
        self.assertEqual(end["icu_bed"]["occupied"], before["icu_bed"]["occupied"])
        self.assertEqual(end["ventilator"]["occupied"], before["ventilator"]["occupied"])
        notes = [n["title"] for n in call("GET", "/notifications", COORD)["items"]]
        self.assertIn("Referral accepted", notes)
        self.assertGreater(call("GET", "/notifications", COORD)["unread"], 0)
        call("POST", "/notifications/read-all", COORD)
        self.assertEqual(call("GET", "/notifications", COORD)["unread"], 0)

    def test_08_no_double_booking_and_rejection(self):
        icu = next(c for c in call("GET", "/hospital/capacity", STAFF1) if c["resource_type"] == "icu_bed")
        # shrink to exactly one free ICU bed
        call("PATCH", f"/hospital/capacity/{icu['capacity_id']}", STAFF1, {"occupied": icu["total_capacity"] - icu["reserved"] - 1})
        hid = next(h["hospital_id"] for h in call("POST", "/search", COORD, {**SEARCH, "needs_ventilator": False}) if h["hospital_name"].startswith("Indus"))

        def new_req():
            return call("POST", "/requests", COORD, {"patient_reference": "PT-X", "resource_type": "icu_bed",
                                                      "latitude": 25.39, "longitude": 68.36})["request_id"]
        a, b = new_req(), new_req()
        call("POST", f"/requests/{a}/send", COORD, {"hospital_id": hid})
        self.assertIn("No capacity", fail(409, "POST", f"/requests/{b}/send", COORD, {"hospital_id": hid}))
        self.assertEqual(call("GET", f"/requests/{b}", COORD)["request"]["request_status"], "no_capacity")
        call("POST", f"/requests/{a}/reject", STAFF1, {"reason": "No specialist"})
        self.assertIn("No specialist", call("GET", f"/requests/{a}", COORD)["request"]["rejection_reason"])
        call("POST", f"/requests/{b}/send", COORD, {"hospital_id": hid})                  # bed was released
        call("POST", f"/requests/{b}/cancel", COORD)
        self.assertEqual(next(c for c in call("GET", "/hospital/capacity", STAFF1)
                              if c["resource_type"] == "icu_bed")["available"], 1)

    def test_09_hold_expires_automatically(self):
        hid = next(h["hospital_id"] for h in call("POST", "/search", COORD, {**SEARCH, "needs_ventilator": False}) if h["hospital_name"].startswith("Jamshoro"))
        rid = call("POST", "/requests", COORD, {"patient_reference": "EXP", "resource_type": "icu_bed",
                                                 "latitude": 25.39, "longitude": 68.36})["request_id"]
        call("POST", f"/requests/{rid}/send", COORD, {"hospital_id": hid})
        with db.tx() as c:   # pretend 20 minutes passed
            c.run("UPDATE reservations SET expires_at = ? WHERE request_id = ?",
                  (db.ts(db.utc_now() - timedelta(minutes=5)), rid))
        maintenance.run(force=True)
        self.assertEqual(call("GET", f"/requests/{rid}", COORD)["request"]["request_status"], "expired")
        icu = next(c for c in router.dispatch("GET", "/hospital/capacity", user_id=IDS["staff3@demo.local"])
                   if c["resource_type"] == "icu_bed")
        self.assertEqual(icu["reserved"], 0)

    # ---------------------------------------------------------------- hospital staff / admin
    def test_10_capacity_validation_and_flagging(self):
        icu = next(c for c in call("GET", "/hospital/capacity", "staff3@demo.local") if c["resource_type"] == "icu_bed")
        fail(422, "PATCH", f"/hospital/capacity/{icu['capacity_id']}", "staff3@demo.local", {"occupied": 999})
        fail(404, "PATCH", f"/hospital/capacity/{icu['capacity_id']}", STAFF1, {"occupied": 1})   # someone else's row
        call("PATCH", f"/hospital/capacity/{icu['capacity_id']}", "staff3@demo.local", {"occupied": 1})   # big drop -> flagged
        flagged = call("GET", "/admin/flagged", ADMIN)
        self.assertGreaterEqual(len(flagged), 2)
        mine = next(f for f in flagged if f["new_occupied"] == 1)
        call("POST", f"/admin/flagged/{mine['log_id']}/review", ADMIN, {"action": "revert"})
        again = next(c for c in call("GET", "/hospital/capacity", "staff3@demo.local") if c["resource_type"] == "icu_bed")
        self.assertEqual(again["occupied"], icu["occupied"])

    def test_11_dashboard_services_admin_views(self):
        d = call("GET", "/hospital/dashboard", STAFF1)
        self.assertGreater(d["total_beds"], 0)
        self.assertIn("avg_response_min", d)
        sv = call("GET", "/hospital/services", STAFF1)
        call("PUT", f"/hospital/services/{sv[0]['service_id']}", STAFF1, {"is_available": False})
        call("PATCH", "/hospital/emergency", STAFF1, {"emergency_available": True})
        self.assertTrue(call("GET", "/hospital/outlook", STAFF1))
        self.assertTrue(call("GET", "/hospital/trends", STAFF1))
        ov = call("GET", "/analytics/overview", ADMIN)
        self.assertTrue(ov["highest_occupancy"] and ov["areas_low_capacity"])
        self.assertGreaterEqual(ov["avg_response_min"], 3)
        dm = call("GET", "/analytics/demand", ADMIN)
        self.assertEqual(len(dm["by_hour"]), 24)
        self.assertTrue(dm["peak_emergency_periods"])
        self.assertTrue(call("GET", "/analytics/trends", ADMIN, params={"days": "7"}))
        self.assertTrue(call("GET", "/analytics/heatmap", COORD))
        self.assertTrue(call("GET", "/analytics/outlook/1", ADMIN))
        self.assertTrue(call("GET", "/admin/records-review", ADMIN))
        self.assertTrue(call("GET", "/admin/audit-log", ADMIN))
        self.assertTrue(call("GET", "/admin/login-log", ADMIN))
        self.assertTrue(call("GET", "/admin/hospitals", ADMIN))
        call("POST", "/admin/hospitals", ADMIN, {"hospital_name": "Added Hospital", "area": "Kotri",
                                                  "location": "Main road", "latitude": 25.3, "longitude": 68.3, "verified": True})
        call("PATCH", "/admin/hospitals/1", ADMIN, {"contact": "123"})
        fail(403, "GET", "/analytics/overview", COORD)

    def test_12_demand_pattern_and_forecast(self):
        fc = call("GET", "/hospital/outlook", STAFF1)["icu_bed"]["forecast"]
        self.assertEqual(len(fc), 24)
        self.assertTrue(all(0 <= x["occupancy_pct"] <= 100 for x in fc))

    def test_12b_every_request_the_coordinator_can_open_has_history(self):
        """Regression: opening an admitted demo patient crashed because it had no status history."""
        mine = call("GET", "/requests/mine", COORD)
        self.assertGreater(len(mine), 50)
        empty = [r["patient_reference"] for r in mine
                 if not call("GET", f"/requests/{r['request_id']}", COORD)["history"]]
        self.assertEqual(empty, [])
        adm = next(r for r in mine if r["patient_reference"].startswith("ADM-"))
        self.assertEqual(call("GET", f"/requests/{adm['request_id']}", COORD)["history"][-1]["status"], "admitted")

    def test_12c_qr_confirmation_messages_and_links(self):
        hid = next(h["hospital_id"] for h in call("POST", "/search", COORD, {**SEARCH, "needs_ventilator": False})
                   if h["hospital_name"].startswith("Indus"))
        rid = call("POST", "/requests", COORD, {"patient_reference": "QR-TEST", "resource_type": "general_bed",
                                                 "latitude": 25.39, "longitude": 68.36})["request_id"]
        code = call("GET", f"/requests/{rid}", COORD)["request"]["request_code"]
        call("POST", f"/requests/{rid}/send", COORD, {"hospital_id": hid})
        self.assertIn("Accept the referral first", fail(409, "POST", "/requests/confirm-qr", STAFF1, {"code": code}))
        call("POST", f"/requests/{rid}/accept", STAFF1)
        self.assertIn("Unknown", fail(404, "POST", "/requests/confirm-qr", STAFF1, {"code": "NOPE1234"}))
        fail(403, "POST", "/requests/confirm-qr", "staff3@demo.local", {"code": code})        # another hospital
        fail(403, "POST", "/requests/confirm-qr", COORD, {"code": code})                      # not hospital staff
        link = f"https://my-app.streamlit.app/?referral={code.lower()}"                       # what the QR contains
        call("POST", "/requests/confirm-qr", STAFF1, {"code": link})
        self.assertEqual(call("GET", f"/requests/{rid}", COORD)["request"]["request_status"], "patient_transferred")
        self.assertIn("already confirmed", fail(409, "POST", "/requests/confirm-qr", STAFF1, {"code": code}))

    def test_12d_scanning_a_qr_always_shows_the_referral(self):
        hid = next(h["hospital_id"] for h in call("POST", "/search", COORD, {**SEARCH, "needs_ventilator": False})
                   if h["hospital_name"].startswith("Indus"))
        rid = call("POST", "/requests", COORD, {"patient_reference": "QR-LOOK", "resource_type": "general_bed",
                                                 "latitude": 25.39, "longitude": 68.36})["request_id"]
        code = call("GET", f"/requests/{rid}", COORD)["request"]["request_code"]
        call("POST", f"/requests/{rid}/send", COORD, {"hospital_id": hid})
        info = call("GET", "/requests/lookup", STAFF1, params={"code": f"https://x.streamlit.app/?referral={code}"})
        self.assertFalse(info["can_confirm"])
        self.assertIn("Not accepted yet", info["message"])
        call("POST", f"/requests/{rid}/accept", STAFF1)
        self.assertTrue(call("GET", "/requests/lookup", STAFF1, params={"code": code})["can_confirm"])
        fail(403, "GET", "/requests/lookup", "staff3@demo.local", params={"code": code})      # another hospital
        fail(403, "GET", "/requests/lookup", COORD, params={"code": code})
        fail(404, "GET", "/requests/lookup", STAFF1, params={"code": "NOPE1234"})
        call("POST", "/requests/confirm-qr", STAFF1, {"code": code})
        self.assertIn("already confirmed", call("GET", "/requests/lookup", STAFF1, params={"code": code})["message"])
        # a demo patient who is already admitted still has a code that shows a clear answer
        adm = next(r for r in call("GET", "/requests/mine", COORD) if r["patient_reference"].startswith("ADM-1"))
        acode = call("GET", f"/requests/{adm['request_id']}", COORD)["request"]["request_code"]
        out = call("GET", "/requests/lookup", STAFF1, params={"code": acode})
        self.assertEqual(out["request_status"], "admitted")
        self.assertIn("already admitted", out["message"])
        self.assertFalse(out["can_confirm"])

    def test_13_reset_demo(self):
        call("POST", "/admin/reset-demo", ADMIN)
        self.assertEqual(len(call("GET", "/admin/hospitals", ADMIN)), 8)
        login(PATIENT)


class PureLogicTests(unittest.TestCase):
    def test_ai(self):
        r = ai_tools.classify("Premature newborn, 3 days old")
        self.assertEqual(r["resource_type"], "nicu_bed")
        self.assertNotEqual(ai_tools.classify("It was difficult, mild routine follow-up")["resource_type"], "icu_bed")
        self.assertNotEqual(ai_tools.classify("patient is stable")["resource_type"], "trauma")

    def test_matcher_and_forecast(self):
        cap = {"available": 3, "occupied": 17, "total_capacity": 20}
        near = matcher.score_hospital(3, cap, cap, cap, True, True, True, 5)
        far = matcher.score_hospital(10, cap, cap, cap, True, True, True, 5)
        self.assertGreater(near, far)
        self.assertEqual(matcher.travel_minutes(40), 60)
        self.assertEqual(forecasting.limited_threshold(20), 2)


if __name__ == "__main__":
    unittest.main()

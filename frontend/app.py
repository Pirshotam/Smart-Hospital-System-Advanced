"""Smart Hospital Bed & Emergency Capacity System - standalone Streamlit app.
Run:   streamlit run frontend/app.py      (nothing else to start - the database creates itself)

Pages are shown per role as a convenience. The real access control is enforced in
hospital_core/router.py: a user who somehow reaches a page still cannot read or change data
that their role does not allow."""
import streamlit as st

import api
from hospital_core import db
from views import (admin_view, auth_view, common, find_view, hospital_view, map_view,
                   requests_view)

st.set_page_config(page_title="Smart Hospital Capacity", page_icon="🏥", layout="wide")

if not db.is_ready():
    with st.spinner("Preparing the demo database (first start only)..."):
        db.ensure_ready()

if st.query_params.get("referral"):           # opened from a referral QR code
    st.session_state["scan_code"] = str(st.query_params["referral"]).strip()
    del st.query_params["referral"]

if "token" not in st.session_state:
    if st.session_state.get("scan_code"):
        st.info("📷 Referral QR code scanned. Log in as hospital staff to confirm the patient's arrival.")
    auth_view.show()
    st.stop()

me, err = api.get("/auth/me")          # refresh role / verification status on every run
if err or not me:
    api.logout(err or "Please log in again.")
    st.rerun()
st.session_state["user"] = me

PAGES = {
    "patient": {
        "🔍 Find a hospital": find_view.page, "📋 My requests": requests_view.page,
        "🗺️ Live hospital map": map_view.live_map, "🔑 Account": auth_view.account},
    "coordinator": {
        "🔍 Find a hospital": find_view.page, "📋 My requests": requests_view.page,
        "🗺️ Live hospital map": map_view.live_map, "🔥 Regional heatmap": map_view.heatmap,
        "🔑 Account": auth_view.account},
    "hospital_staff": {
        "📊 Dashboard": hospital_view.dashboard, "🛏️ Capacity": hospital_view.capacity,
        "📥 Referrals": hospital_view.referrals, "🧑‍⚕️ Admitted patients": hospital_view.admitted,
        "⚙️ Services & ED": hospital_view.services, "📈 Forecast & trends": hospital_view.forecast,
        "🔑 Account": auth_view.account},
    "admin": {
        "🛡️ Overview": admin_view.overview, "🏥 Hospitals": admin_view.hospitals,
        "👥 Users": admin_view.users, "🔎 Review & audit": admin_view.review,
        "📈 Analytics": admin_view.analytics, "🗺️ Live map": map_view.live_map,
        "🔥 Regional heatmap": map_view.heatmap, "🔑 Account": auth_view.account},
}

ROLE_NAMES = {"patient": "Patient / Attendant", "coordinator": "Emergency Coordinator",
              "hospital_staff": "Hospital Staff", "admin": "Administrator"}

st.sidebar.title("🏥 Smart Hospital Capacity")
st.sidebar.write(f"**{me['full_name']}**  \n{ROLE_NAMES[me['role']]}")
if me.get("hospital"):
    h = me["hospital"]
    badge = {"verified": "✅ verified", "pending": "⏳ awaiting verification", "rejected": "❌ rejected"}
    st.sidebar.caption(f"{h['hospital_name']} · {badge[h['verification_status']]}")
common.notifications_sidebar()
pages = PAGES[me["role"]]
if st.session_state.get("scan_code"):
    if me["role"] == "hospital_staff":
        st.session_state["nav"] = "📥 Referrals"      # the page prefills the code and asks to confirm
    else:
        st.warning("That QR code is for the receiving hospital's staff, who check it and confirm the patient's arrival.")
        st.session_state.pop("scan_code")
choice = st.sidebar.radio("Go to", list(pages), key="nav", label_visibility="collapsed")
if st.sidebar.button("Log out"):
    api.logout()
    st.rerun()
pages[choice]()

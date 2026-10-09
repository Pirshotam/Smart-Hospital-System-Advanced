"""Login, registration and account pages."""
import streamlit as st

import api

ROLE_CHOICES = {"Patient / Attendant": "patient",
                "Ambulance / Emergency Coordinator": "coordinator",
                "Hospital Staff": "hospital_staff"}


def show():
    st.title("🏥 Smart Hospital Bed & Emergency Capacity System")
    st.caption("Find a hospital with the right beds, ICU and ventilators - without calling around.")
    if st.session_state.get("flash"):
        st.warning(st.session_state.pop("flash"))
    tab_login, tab_register = st.tabs(["Log in", "Create account"])
    with tab_login:
        _login()
    with tab_register:
        _register()


DEMO_ACCOUNTS = """| Role | Email | What to try |
|---|---|---|
| Administrator | `admin@demo.local` | approve users, verify hospitals, analytics |
| Emergency coordinator | `coordinator@demo.local` | search, refer, dispatch ambulance |
| Patient / attendant | `patient@demo.local` | search and track a request |
| Hospital staff | `staff1@demo.local` ... `staff7@demo.local` | capacity, referrals, admitted patients |
| Staff (pending approval) | `staff8@demo.local` | log in as admin first, approve + verify |

Password for every demo account: **Demo@1234**"""


def _login():
    with st.expander("🔑 Demo accounts"):
        st.markdown(DEMO_ACCOUNTS)
        st.caption("This is a demo with sample data. The database lives on the server and is reset when the "
                   "app restarts (an administrator can also reset it from the Overview page).")
    with st.form("login"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        go = st.form_submit_button("Log in", type="primary")
    if go:
        data, err = api.post("/auth/login", auth=False, json={"email": email, "password": password})
        if err:
            st.error(err)
            return
        st.session_state["token"] = data["access_token"]
        st.session_state["user"] = data["user"]
        st.rerun()


def _register():
    role_label = st.selectbox("I am a", list(ROLE_CHOICES), key="reg_role")
    role = ROLE_CHOICES[role_label]
    if role != "patient":
        st.info("Coordinator and hospital staff accounts must be approved by an administrator before "
                "you can log in. Hospitals must also be verified before they appear in searches.")
    hospital_id, new_hospital = None, None
    if role == "hospital_staff":
        mode = st.radio("Your hospital", ["Join an existing hospital", "Register a new hospital"], key="reg_mode")
        if mode == "Join an existing hospital":
            hospitals, _ = api.get("/auth/hospitals", auth=False)
            options = {f"{h['hospital_name']} ({h['area']})": h["hospital_id"] for h in hospitals or []}
            if options:
                hospital_id = options[st.selectbox("Hospital", list(options), key="reg_hospital")]
        else:
            c1, c2 = st.columns(2)
            name = c1.text_input("Hospital name", key="nh_name")
            area = c2.text_input("Area (e.g. Latifabad)", key="nh_area")
            address = st.text_input("Address", key="nh_addr")
            c3, c4, c5 = st.columns(3)
            lat = c3.number_input("Latitude", value=25.39, format="%.4f", key="nh_lat")
            lon = c4.number_input("Longitude", value=68.36, format="%.4f", key="nh_lon")
            contact = c5.text_input("Contact number", key="nh_contact")
            new_hospital = {"hospital_name": name, "area": area, "location": address,
                            "latitude": lat, "longitude": lon, "contact": contact or None}
    with st.form("register"):
        full_name = st.text_input("Full name")
        email = st.text_input("Email address")
        phone = st.text_input("Phone (optional)")
        pw1 = st.text_input("Password (8+ characters, letters and numbers)", type="password")
        pw2 = st.text_input("Confirm password", type="password")
        go = st.form_submit_button("Create account", type="primary")
    if go:
        if pw1 != pw2:
            st.error("Passwords do not match.")
            return
        body = {"full_name": full_name, "email": email, "password": pw1, "phone": phone or None, "role": role,
                "hospital_id": hospital_id, "new_hospital": new_hospital}
        data, err = api.post("/auth/register", auth=False, json=body)
        if err:
            st.error(err)
        else:
            st.success(data["message"])


def account():
    user = st.session_state["user"]
    st.header("🔑 Account")
    st.write(f"**{user['full_name']}**  \n{user['email']}  \nRole: {user['role'].replace('_', ' ')}")
    st.subheader("Change password")
    with st.form("pw"):
        old = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password")
        go = st.form_submit_button("Change password")
    if go:
        data, err = api.post("/auth/change-password", json={"old_password": old, "new_password": new})
        st.error(err) if err else st.success("Password changed.")

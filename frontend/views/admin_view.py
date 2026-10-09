"""Administrator pages: overview, hospitals, users, review & audit, analytics."""
import pandas as pd
import streamlit as st

import api
from views import analytics_view as av
from views import common as c

ROLES = ["patient", "coordinator", "hospital_staff", "admin"]


def overview():
    st.header("🛡️ System overview")
    d, err = api.get("/analytics/overview")
    if c.show_error(err):
        return
    beds = d["beds"] or {}
    users = pd.DataFrame(d["users"]) if d["users"] else pd.DataFrame(columns=["role", "status", "n"])
    pending_users = int(users.loc[users["status"] == "pending", "n"].sum()) if not users.empty else 0
    pending_h = sum(x["n"] for x in d["hospitals"] if x["verification_status"] == "pending")
    a, b, cc, dd = st.columns(4)
    a.metric("Total beds in system", int(beds.get("total") or 0))
    b.metric("Free beds", int(beds.get("available") or 0))
    cc.metric("Avg hospital response", f"{d['avg_response_min']} min" if d["avg_response_min"] else "-")
    dd.metric("Requests (all time)", sum(x["n"] for x in d["requests_by_status"]))
    e, f = st.columns(2)
    e.metric("Accounts awaiting approval", pending_users)
    f.metric("Hospitals awaiting verification", int(pending_h))
    if pending_users or pending_h:
        st.warning("There are accounts or hospitals waiting for you - see **Hospitals** and **Users**.")

    left, right = st.columns(2)
    with left:
        st.subheader("Hospitals with highest occupancy")
        hi = pd.DataFrame(d["highest_occupancy"])
        if not hi.empty:
            hi["occupancy_pct"] = hi["occupancy_pct"].astype(float)
            st.bar_chart(hi.set_index("hospital_name")["occupancy_pct"], y_label="Occupancy %")
    with right:
        st.subheader("Requests by status")
        bs = pd.DataFrame(d["requests_by_status"])
        if not bs.empty:
            bs["request_status"] = bs["request_status"].map(c.status_text)
            st.bar_chart(bs.set_index("request_status")["n"])
    st.subheader("Areas with low hospital capacity")
    ar = pd.DataFrame(d["areas_low_capacity"])
    if not ar.empty:
        ar["free_pct"] = ar["free_pct"].astype(float)
        st.dataframe(ar.rename(columns={"area": "Area", "hospitals": "Hospitals", "available_beds": "Free beds",
                                        "total_beds": "Total beds", "free_pct": "Free %"}), hide_index=True)
    st.subheader("Average response time by hospital")
    rt = pd.DataFrame(d["response_by_hospital"])
    if not rt.empty:
        rt["avg_response_min"] = rt["avg_response_min"].astype(float)
        st.bar_chart(rt.set_index("hospital_name")["avg_response_min"], y_label="Minutes")

    with st.expander("⚠️ Reset the demo data"):
        st.caption("Deletes everything (accounts, requests, changes) and loads the original demo data. "
                   "All demo passwords return to Demo@1234.")
        if st.checkbox("I understand this erases all data", key="reset_ok") and st.button("Reset demo data"):
            _, err = api.post("/admin/reset-demo")
            if err:
                st.error(err)
            else:
                st.success("Demo data restored.")
                st.rerun()


def hospitals():
    st.header("🏥 Hospitals")
    rows, err = api.get("/admin/hospitals")
    if c.show_error(err):
        return
    for h in rows:
        with st.container(border=True):
            a, b = st.columns([3, 2])
            icon = {"verified": "✅", "pending": "⏳", "rejected": "❌"}[h["verification_status"]]
            a.markdown(f"{icon} **{h['hospital_name']}** · {h['area']}  \n{h['location']} · {h['contact'] or 'no contact'}  \n"
                       f"Account: **{h['account_status']}** · staff accounts: {h['staff_accounts']} · "
                       f"resources: {h['resources']} · last update: {c.ago(h['minutes_since_update'])}")
            hid = h["hospital_id"]
            k = b.columns(3)
            if h["verification_status"] != "verified" and k[0].button("Verify", key=f"v{hid}", type="primary"):
                api.post(f"/admin/hospitals/{hid}/verify")
                st.rerun()
            if h["verification_status"] == "pending" and k[1].button("Reject", key=f"r{hid}"):
                api.post(f"/admin/hospitals/{hid}/reject")
                st.rerun()
            nxt = "inactive" if h["account_status"] == "active" else "active"
            if k[2].button("Deactivate" if nxt == "inactive" else "Activate", key=f"s{hid}"):
                api.patch(f"/admin/hospitals/{hid}", json={"account_status": nxt})
                st.rerun()
    with st.expander("➕ Add a hospital"):
        with st.form("addh"):
            x, y = st.columns(2)
            name = x.text_input("Hospital name")
            area = y.text_input("Area")
            addr = st.text_input("Address")
            p, q, r = st.columns(3)
            lat = p.number_input("Latitude", value=25.39, format="%.4f")
            lon = q.number_input("Longitude", value=68.36, format="%.4f")
            contact = r.text_input("Contact")
            verified = st.checkbox("Mark as verified")
            if st.form_submit_button("Add hospital"):
                _, err = api.post("/admin/hospitals", json={
                    "hospital_name": name, "area": area, "location": addr, "latitude": lat, "longitude": lon,
                    "contact": contact or None, "verified": verified})
                st.error(err) if err else st.rerun()


def users():
    st.header("👥 Users & permissions")
    f1, f2 = st.columns(2)
    role = f1.selectbox("Role", ["all"] + ROLES)
    status = f2.selectbox("Status", ["all", "pending", "active", "suspended"])
    params = {k: v for k, v in {"role": role, "status": status}.items() if v != "all"}
    rows, err = api.get("/admin/users", params=params)
    if c.show_error(err):
        return
    me = st.session_state["user"]["user_id"]
    for u in rows:
        with st.container(border=True):
            a, b = st.columns([3, 2])
            a.markdown(f"**{u['full_name']}** · {u['email']}  \n{u['role'].replace('_', ' ')}"
                       f"{' · ' + u['hospital_name'] if u['hospital_name'] else ''} · "
                       f"status **{u['status']}** · last login {c.fmt_time(u['last_login'])}")
            if u["user_id"] == me:
                b.caption("This is you")
                continue
            k = b.columns(3)
            if u["status"] != "active" and k[0].button("Approve", key=f"ap{u['user_id']}", type="primary"):
                api.patch(f"/admin/users/{u['user_id']}", json={"status": "active"})
                st.rerun()
            if u["status"] == "active" and k[1].button("Suspend", key=f"su{u['user_id']}"):
                api.patch(f"/admin/users/{u['user_id']}", json={"status": "suspended"})
                st.rerun()
            with k[2].popover("More"):
                new_role = st.selectbox("Role", ROLES, index=ROLES.index(u["role"]), key=f"role{u['user_id']}")
                if st.button("Change role", key=f"cr{u['user_id']}"):
                    _, err = api.patch(f"/admin/users/{u['user_id']}", json={"role": new_role})
                    st.error(err) if err else st.rerun()
                pw = st.text_input("New password", type="password", key=f"pw{u['user_id']}")
                if st.button("Reset password", key=f"rp{u['user_id']}"):
                    _, err = api.post(f"/admin/users/{u['user_id']}/reset-password", json={"new_password": pw})
                    st.error(err) if err else st.success("Password reset.")
    with st.expander("➕ Create a user"):
        hospitals_, _ = api.get("/admin/hospitals")
        hmap = {h["hospital_name"]: h["hospital_id"] for h in hospitals_ or []}
        with st.form("newuser"):
            name = st.text_input("Full name")
            email = st.text_input("Email")
            pw = st.text_input("Temporary password", type="password")
            r = st.selectbox("Role", ROLES, key="nu_role")
            h = st.selectbox("Hospital (hospital staff only)", ["-"] + list(hmap))
            if st.form_submit_button("Create user"):
                _, err = api.post("/admin/users", json={"full_name": name, "email": email, "password": pw,
                                                        "role": r, "hospital_id": hmap.get(h)})
                st.error(err) if err else st.rerun()


def review():
    st.header("🔎 Review & audit")
    t1, t2, t3, t4 = st.tabs(["Flagged updates", "Records to review", "Capacity audit log", "Login activity"])
    with t1:
        rows, err = api.get("/admin/flagged")
        if not c.show_error(err):
            if not rows:
                st.success("No suspicious updates waiting.")
            for r in rows:
                with st.container(border=True):
                    a, b = st.columns([3, 2])
                    a.markdown(f"**{r['hospital_name']}** · {c.res_label(r['resource_type'])}  \n"
                               f"Occupied {r['old_occupied']} → **{r['new_occupied']}** "
                               f"(total {r['old_total']} → {r['new_total']}) by {r['changed_by'] or 'unknown'} · "
                               f"{c.fmt_time(r['changed_at'])}")
                    k = b.columns(2)
                    if k[0].button("Looks fine", key=f"ok{r['log_id']}"):
                        api.post(f"/admin/flagged/{r['log_id']}/review", json={"action": "approve"})
                        st.rerun()
                    if k[1].button("Revert it", key=f"rv{r['log_id']}"):
                        _, err = api.post(f"/admin/flagged/{r['log_id']}/review", json={"action": "revert"})
                        st.error(err) if err else st.rerun()
    with t2:
        rows, err = api.get("/admin/records-review")
        if not c.show_error(err):
            if rows:
                st.dataframe(pd.DataFrame(rows).rename(columns={"type": "Issue", "record": "Record", "detail": "Detail"}),
                             hide_index=True)
            else:
                st.success("No inactive or incorrect records found.")
    with t3:
        rows, err = api.get("/admin/audit-log")
        if not c.show_error(err) and rows:
            df = pd.DataFrame(rows)
            df["when"] = df["changed_at"].map(c.fmt_time)
            df["resource"] = df["resource_type"].map(c.res_label)
            df["flagged"] = df["flagged"].map(lambda x: "⚠️" if x else "")
            st.dataframe(df[["when", "hospital_name", "resource", "old_occupied", "new_occupied", "reason",
                             "changed_by", "flagged", "review_action"]], hide_index=True)
    with t4:
        rows, err = api.get("/admin/login-log")
        if not c.show_error(err) and rows:
            df = pd.DataFrame(rows)
            df["when"] = df["created_at"].map(c.fmt_time)
            df["success"] = df["success"].map(lambda x: "✅" if x else "❌")
            st.dataframe(df[["when", "email", "success", "reason", "ip"]], hide_index=True)


def analytics():
    st.header("📈 Analytics & forecasting")
    t1, t2, t3 = st.tabs(["Capacity trends", "Demand forecasting", "Hospital capacity prediction"])
    with t1:
        days = st.slider("Days", 3, 30, 14)
        rows, err = api.get("/analytics/trends", params={"days": days})
        if not c.show_error(err):
            av.trends_panel(rows)
    with t2:
        d, err = api.get("/analytics/demand")
        if not c.show_error(err):
            av.demand_panel(d)
    with t3:
        hs, _ = api.get("/admin/hospitals")
        verified = {h["hospital_name"]: h["hospital_id"] for h in hs or [] if h["verification_status"] == "verified"}
        if verified:
            name = st.selectbox("Hospital", list(verified))
            out, err = api.get(f"/admin/outlook/{verified[name]}")
            if not c.show_error(err):
                av.outlook_panel(out, key="adm")

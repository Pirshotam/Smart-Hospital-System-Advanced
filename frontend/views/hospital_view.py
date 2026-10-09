"""Hospital staff pages."""
import pandas as pd
import streamlit as st

import api
from views import analytics_view as av
from views import common as c


def _banner():
    """Verification status of the staff member's hospital."""
    h = st.session_state["user"].get("hospital") or {}
    if h.get("verification_status") == "pending":
        st.warning("⏳ Your hospital is waiting for administrator verification. You can look around, but "
                   "capacity changes and referral actions are disabled until it is verified, and patients "
                   "cannot find the hospital yet.")
    elif h.get("verification_status") == "rejected":
        st.error("Your hospital was rejected by an administrator. Contact the system administrator.")
    elif h.get("account_status") and h["account_status"] != "active":
        st.error(f"Your hospital account is {h['account_status']}.")


def dashboard():
    st.header("📊 Hospital dashboard")
    _banner()
    d, err = api.get("/hospital/dashboard")
    if c.show_error(err):
        return
    if d["data_outdated"]:
        st.warning(f"⚠️ Some of your capacity data is {c.ago(d['oldest_update_min'])} old. "
                   "Patients see a 'may be outdated' warning - please update it.")
    a, b, cc, dd = st.columns(4)
    a.metric("Total beds", d["total_beds"])
    b.metric("Occupied beds", d["occupied_beds"])
    cc.metric("Available beds", d["available_beds"])
    dd.metric("Capacity utilization", f"{d['capacity_utilization_pct']}%")
    e, f, g, h = st.columns(4)
    e.metric("ICU occupancy", f"{d['icu_occupancy_pct']}%" if d["icu_occupancy_pct"] is not None else "-")
    f.metric("Emergency requests", d["emergency_requests"])
    g.metric("Accepted referrals", d["accepted_referrals"])
    h.metric("Rejected referrals", d["rejected_referrals"])
    i, j = st.columns(2)
    i.metric("Pending review", d["pending_review"])
    j.metric("Average response time", f"{d['avg_response_min']} min" if d["avg_response_min"] is not None else "-")
    st.subheader("Daily admissions")
    if d["daily_admissions"]:
        st.bar_chart(pd.DataFrame(d["daily_admissions"]).set_index("day")["admissions"])
    else:
        st.caption("No admissions in the last 14 days.")
    st.subheader("Occupancy by resource")
    df = pd.DataFrame(d["resources"])
    if not df.empty:
        df["resource"] = df["resource_type"].map(c.res_label)
        df["occupancy_%"] = (100 * df["occupied"] / df["total_capacity"].where(df["total_capacity"] > 0)).round(1)
        st.bar_chart(df.set_index("resource")["occupancy_%"])


def capacity():
    st.header("🛏️ Capacity")
    _banner()
    rows, err = api.get("/hospital/capacity")
    if c.show_error(err):
        return
    if rows:
        df = pd.DataFrame(rows)
        df["Resource"] = df["resource_type"].map(c.res_label)
        df["Last updated"] = df["minutes_ago"].map(c.ago)
        df["⚠️"] = df["possibly_outdated"].map(lambda x: "may be outdated" if x else "")
        st.dataframe(df[["Resource", "total_capacity", "occupied", "reserved", "unavailable", "available",
                         "Last updated", "⚠️"]].rename(columns={
            "total_capacity": "Total", "occupied": "Occupied", "reserved": "Held (reserved)",
            "unavailable": "Temporarily unavailable", "available": "Available"}), hide_index=True)
        st.caption("Held beds belong to referrals in progress and are released automatically if a request "
                   "expires or is rejected.")
        with st.form("upd"):
            label = st.selectbox("Resource to update", [c.res_label(r["resource_type"]) for r in rows])
            row = next(r for r in rows if c.res_label(r["resource_type"]) == label)
            x, y, z = st.columns(3)
            total = x.number_input("Total", 0, value=int(row["total_capacity"]))
            occ = y.number_input("Occupied", 0, value=int(row["occupied"]))
            unav = z.number_input("Temporarily unavailable", 0, value=int(row["unavailable"]))
            if st.form_submit_button("Update capacity", type="primary"):
                _, err = api.patch(f"/hospital/capacity/{row['capacity_id']}",
                                   json={"total_capacity": total, "occupied": occ, "unavailable": unav})
                st.error(err) if err else st.rerun()
    else:
        st.info("No resources yet. Add your first one below.")
    existing = {r["resource_type"] for r in rows}
    missing = [lbl for lbl, v in c.RESOURCES.items() if v not in existing]
    if missing:
        with st.expander("➕ Add a resource type"):
            with st.form("add"):
                lbl = st.selectbox("Resource", missing)
                t = st.number_input("Total capacity", 0, value=10)
                o = st.number_input("Currently occupied", 0, value=0)
                if st.form_submit_button("Add resource"):
                    _, err = api.post("/hospital/capacity", json={"resource_type": c.RESOURCES[lbl],
                                                                 "total_capacity": t, "occupied": o})
                    st.error(err) if err else st.rerun()


ACTIONS = {"request_sent": [("Start review", "review"), ("Accept", "accept"), ("Reject", "reject")],
           "hospital_reviewing": [("Accept", "accept"), ("Reject", "reject")],
           "accepted": [("Confirm transfer", "transfer")],
           "patient_transferred": [("Admit", "admit")]}


@st.fragment(run_every=10)
def _inbox():
    rows, err = api.get("/hospital/requests")
    if c.show_error(err):
        return
    if not rows:
        st.info("No referrals yet.")
        return
    for r in rows:
        with st.container(border=True):
            left, right = st.columns([3, 2])
            need = c.res_label(r["required_resource"]) + (" + ventilator" if r["needs_ventilator"] else "")
            left.markdown(f"**#{r['request_id']} · {r['patient_reference']}** · {need}  \n"
                          f"urgency **{r['urgency']}** · {c.fmt_time(r['created_at'])}"
                          + (f" · {r['required_service']}" if r["required_service"] else ""))
            if r.get("case_summary"):
                left.caption(r["case_summary"])
            if r.get("ambulance_unit"):
                left.caption(f"🚑 {r['ambulance_unit']} on the way (ETA ~{r['transfer_eta_min']} min)")
            right.markdown(f"Status: **{c.status_text(r['request_status'])}**")
            left_min = c.minutes_left(r.get("reserved_until"))
            if left_min is not None:
                right.caption(f"Bed held for ~{left_min} more min")
            if r["request_status"] == "rejected" and r.get("rejection_reason"):
                right.caption(f"Reason: {r['rejection_reason']}")
            acts = ACTIONS.get(r["request_status"], [])
            reason = None
            if any(p == "reject" for _, p in acts):
                reason = right.text_input("Rejection reason (optional)", key=f"why{r['request_id']}")
            for k, (label, path) in zip(right.columns(len(acts) or 1), acts):
                if k.button(label, key=f"{path}{r['request_id']}"):
                    body = {"reason": reason} if path == "reject" else None
                    _, err = api.post(f"/requests/{r['request_id']}/{path}", **({"json": body} if body else {}))
                    if err:
                        st.error(err)
                    else:
                        st.rerun()


def referrals():
    st.header("📥 Referrals")
    _banner()
    st.caption("Updates automatically every 10 seconds. Accepting restarts the bed hold so the patient has "
               "time to arrive.")
    scanned = st.session_state.pop("scan_code", None)
    if scanned:
        st.session_state["qr_code_input"] = scanned
        st.session_state["qr_do_lookup"] = True
    auto_lookup = st.session_state.pop("qr_do_lookup", False)
    with st.expander("📷 Check a referral code / confirm patient arrival (scan the QR or type the code)",
                     expanded=bool(st.session_state.get("qr_info") or auto_lookup)):
        code = st.text_input("Referral code (or the scanned link)", key="qr_code_input")
        if st.button("Check code") or auto_lookup:
            if code.strip():
                info, err = api.get("/requests/lookup", params={"code": code})
                st.session_state["qr_info"], st.session_state["qr_err"] = info, err
        err = st.session_state.get("qr_err")
        info = st.session_state.get("qr_info")
        if err:
            st.error(err)
        if info:
            with st.container(border=True):
                need = c.res_label(info["required_resource"]) + (" + ventilator" if info["needs_ventilator"] else "")
                st.markdown(f"**#{info['request_id']} · {info['patient_reference']}** · {need} · urgency "
                            f"**{info['urgency']}**  \nStatus: **{c.status_text(info['request_status'])}**")
                if info.get("case_summary"):
                    st.caption(info["case_summary"])
                (st.success if info["request_status"] in ("accepted", "patient_transferred", "admitted")
                 else st.warning)(info["message"])
                if info["can_confirm"] and st.button("✅ Confirm patient arrival", type="primary", key="qr_confirm"):
                    out, err2 = api.post("/requests/confirm-qr", json={"code": info["request_code"]})
                    if err2:
                        st.error(err2)
                    else:
                        st.session_state["qr_info"], st.session_state["qr_err"] = None, None
                        st.success(f"Arrival confirmed for request #{out['request_id']}. "
                                   "You can now admit the patient below.")
    _inbox()


def admitted():
    st.header("🧑‍⚕️ Admitted patients")
    _banner()
    rows, err = api.get("/hospital/admitted")
    if c.show_error(err):
        return
    if not rows:
        st.info("No admitted patients right now.")
        return
    for r in rows:
        with st.container(border=True):
            a, b = st.columns([4, 1])
            a.markdown(f"**{r['patient_reference']}** · {c.res_label(r['required_resource'])}"
                       f"{' + ventilator' if r['needs_ventilator'] else ''} · admitted {c.fmt_time(r['admitted_at'])}")
            if r.get("case_summary"):
                a.caption(r["case_summary"])
            if b.button("Discharge", key=f"dis{r['request_id']}"):
                _, err = api.post(f"/hospital/admitted/{r['request_id']}/discharge")
                st.error(err) if err else st.rerun()


def services():
    st.header("⚙️ Services & emergency department")
    _banner()
    me, _ = api.get("/hospital/me")
    if me:
        on = st.toggle("Emergency department accepting patients", value=bool(me["emergency_available"]))
        if on != bool(me["emergency_available"]):
            _, err = api.patch("/hospital/emergency", json={"emergency_available": on})
            st.error(err) if err else st.rerun()
    st.subheader("Specialist services")
    rows, err = api.get("/hospital/services")
    if c.show_error(err):
        return
    for r in rows:
        current = bool(r["is_available"])
        new = st.toggle(r["service_name"], value=current, key=f"svc{r['service_id']}")
        if new != current:
            _, err = api.put(f"/hospital/services/{r['service_id']}", json={"is_available": new})
            st.error(err) if err else st.rerun()


def forecast():
    st.header("📈 Forecast & trends")
    out, err = api.get("/hospital/outlook")
    if not c.show_error(err):
        av.outlook_panel(out)
    st.subheader("Capacity trends by date")
    t, err = api.get("/hospital/trends")
    if not c.show_error(err):
        av.trends_panel(t)

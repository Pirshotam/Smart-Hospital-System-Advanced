"""My requests: status tracking, QR code, transfer confirmation and ambulance tracking."""
import io

import pandas as pd
import segno
import streamlit as st

import api
from views import common as c

QR_STATUSES = ("request_sent", "hospital_reviewing", "accepted", "patient_transferred", "admitted")
CANCELLABLE = ("searching", "no_capacity", "request_sent", "hospital_reviewing", "accepted")


def _qr(code):
    buf = io.BytesIO()
    segno.make(f"REFERRAL:{code}", error="m").save(buf, kind="png", scale=6, border=2)
    return buf.getvalue()


def page():
    st.header("📋 My requests")
    rows, err = api.get("/requests/mine")
    if c.show_error(err):
        return
    if not rows:
        st.info("You have not sent any requests yet. Use **Find a hospital**.")
        return
    if st.button("🔄 Refresh"):
        st.rerun()
    df = pd.DataFrame(rows)
    df["status"] = df["request_status"].map(c.status_text)
    df["need"] = df["required_resource"].map(c.res_label)
    df["created"] = df["created_at"].map(c.fmt_time)
    st.dataframe(df[["request_id", "patient_reference", "need", "urgency", "status", "hospital_name", "created"]],
                 hide_index=True)
    options = {f"#{r['request_id']} · {r['patient_reference']} · {c.status_text(r['request_status'])}": r["request_id"]
               for r in rows}
    rid = options[st.selectbox("Open a request", list(options))]
    _detail(rid)


def _detail(rid):
    data, err = api.get(f"/requests/{rid}")
    if c.show_error(err):
        return
    req, hist, hosp = data["request"], data["history"], data["hospital"]
    status = req["request_status"]
    user = st.session_state["user"]

    if status in c.STEPS:
        i = c.STEPS.index(status)
        st.progress((i + 1) / len(c.STEPS), text=f"{c.status_text(status)} (step {i + 1} of {len(c.STEPS)})")
    else:
        st.error(f"Final status: {c.status_text(status)}" + (f" - {req['rejection_reason']}" if req.get("rejection_reason") else ""))

    left = c.minutes_left(req.get("reserved_until"))
    if left is not None and status in ("request_sent", "hospital_reviewing", "accepted"):
        st.info(f"🛏️ A bed is held for you for about **{left} more minute(s)**. If the hold runs out the bed is released.")
    if hosp:
        st.write(f"**Hospital:** {hosp['hospital_name']} ({hosp['area']}) · {hosp['contact'] or 'no contact listed'}")
    if req.get("case_summary"):
        st.write(f"**Case summary:** {req['case_summary']}")

    if req.get("request_code") and status in QR_STATUSES:
        link = f"{c.app_url()}/?referral={req['request_code']}"
        a, b = st.columns([1, 2])
        a.image(_qr(link), width=200)
        what = {"request_sent": "The hospital has not accepted yet - the QR becomes useful once it does.",
                "hospital_reviewing": "The hospital has not accepted yet - the QR becomes useful once it does.",
                "accepted": "Show this QR code when the patient reaches the hospital so staff can confirm the arrival.",
                "patient_transferred": "✅ Arrival already confirmed. Scanning it shows the referral details.",
                "admitted": "✅ Patient admitted. Scanning it shows the referral details."}[status]
        b.markdown(f"**Referral code: `{req['request_code']}`**  \n{what}")
        b.caption("Hospital staff scan it with any phone camera: it opens this app, they log in, and see the "
                  "referral. They can also type the code under *Referrals -> Check a referral code*.")

    buttons = st.columns(4)
    if status == "accepted" and buttons[0].button("✅ Confirm patient transferred", key=f"tr{rid}"):
        _, err = api.post(f"/requests/{rid}/transfer")
        st.error(err) if err else st.rerun()
    if status in CANCELLABLE and buttons[1].button("Cancel request", key=f"cx{rid}"):
        _, err = api.post(f"/requests/{rid}/cancel")
        st.error(err) if err else st.rerun()

    if status == "accepted" and user["role"] == "coordinator" and not req.get("dispatched_at"):
        with st.form(f"disp{rid}"):
            unit = st.text_input("Ambulance unit (e.g. AMB-12)")
            if st.form_submit_button("🚑 Dispatch ambulance"):
                out, err = api.post(f"/requests/{rid}/dispatch", json={"ambulance_unit": unit})
                if err:
                    st.error(err)
                else:
                    st.success(f"Dispatched. ETA about {out['eta_min']} min.")
                    st.rerun()
    if req.get("dispatched_at") and status in ("accepted", "patient_transferred", "admitted"):
        st.subheader("🚑 Patient transfer tracking")
        track_panel(rid)

    st.subheader("History")
    if hist:
        h = pd.DataFrame(hist, columns=["changed_at", "status", "note"])
        h["when"] = h["changed_at"].map(c.fmt_time)
        h["status"] = h["status"].map(c.status_text)
        st.dataframe(h[["when", "status", "note"]], hide_index=True)
    else:
        st.caption("No status history was recorded for this request.")


@st.fragment(run_every=5)
def track_panel(rid):
    t, err = api.get(f"/requests/{rid}/tracking")
    if err or not t or not t.get("dispatched"):
        st.caption("Waiting for dispatch...")
        return
    st.progress(t["progress"], text=f"{t['ambulance_unit']} → {t['destination']['name']}")
    if t["arrived"]:
        st.success("The ambulance has arrived at the hospital.")
    else:
        st.write(f"Estimated arrival in **{t['eta_remaining_min']} min**.")
    pts = pd.DataFrame([
        {"latitude": t["latitude"], "longitude": t["longitude"], "color": "#e67e22", "size": 200},
        {"latitude": t["destination"]["latitude"], "longitude": t["destination"]["longitude"],
         "color": "#2ecc71", "size": 160},
        {"latitude": t["origin"]["latitude"], "longitude": t["origin"]["longitude"], "color": "#3498db", "size": 100}])
    st.map(pts, latitude="latitude", longitude="longitude", color="color", size="size")
    st.caption("🟠 ambulance · 🟢 hospital · 🔵 pickup point")

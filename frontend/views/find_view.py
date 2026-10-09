"""Patients and coordinators: search, compare, AI case assistant, send referral."""
import pandas as pd
import streamlit as st

import api
from views import common as c

OTHER = "Custom coordinates"


def _classify():
    text = st.session_state.get("case_text", "").strip()
    if len(text) < 3:
        st.session_state["ai_msg"] = "Type a case description first."
        return
    data, err = api.post("/ai/classify", json={"text": text})
    if err:
        st.session_state["ai_msg"] = err
        return
    st.session_state["f_resource"] = c.res_label(data["resource_type"])
    st.session_state["f_vent"] = data["needs_ventilator"]
    st.session_state["f_urgency"] = data["urgency"]
    if data.get("service") in st.session_state.get("_svc_names", []):
        st.session_state["f_service"] = data["service"]
    st.session_state["ai_cls"] = data
    st.session_state["ai_msg"] = None


def _summarize():
    text = st.session_state.get("case_text", "").strip()
    if len(text) < 3:
        st.session_state["ai_msg"] = "Type a case description first."
        return
    data, err = api.post("/ai/summarize", json={"text": text})
    st.session_state["ai_sum"] = data
    st.session_state["ai_msg"] = err


def _location():
    c1, c2 = st.columns(2)
    area = c1.selectbox("Your current location", list(c.LOCATIONS) + [OTHER], key="f_area")
    if area == OTHER:
        lat = c2.number_input("Latitude", value=25.39, format="%.4f", key="f_lat")
        lon = c2.number_input("Longitude", value=68.36, format="%.4f", key="f_lon")
        return area, lat, lon
    return (area, *c.LOCATIONS[area])


def page():
    st.header("🔍 Find a hospital")
    services, _ = api.get("/services")
    svc = {s["service_name"]: s["service_id"] for s in services or []}
    st.session_state["_svc_names"] = list(svc)

    with st.expander("🤖 AI case assistant - describe the patient (optional)"):
        st.text_area("Patient condition / case description", key="case_text", height=130,
                     placeholder="e.g. 65 year old male, chest pain for 2 hours, BP 90/60, SpO2 88%, diabetic")
        b1, b2 = st.columns(2)
        b1.button("Suggest the facility needed", on_click=_classify)
        b2.button("Summarize the case", on_click=_summarize)
        if st.session_state.get("ai_msg"):
            st.warning(st.session_state["ai_msg"])
        cls = st.session_state.get("ai_cls")
        if cls:
            st.success(f"Suggested: **{c.res_label(cls['resource_type'])}**"
                       f"{' + ventilator' if cls['needs_ventilator'] else ''} · urgency **{cls['urgency']}**"
                       f"{' · ' + cls['service'] if cls['service'] else ''} · confidence {cls['confidence']}%. "
                       "The filters below were filled in for you.")
        summ = st.session_state.get("ai_sum")
        if summ:
            st.info("**Structured summary:** " + summ["summary_text"])

    c1, c2, c3 = st.columns(3)
    c1.selectbox("Required bed / facility type", list(c.RESOURCES), key="f_resource")
    c2.selectbox("Required specialist / service", ["Any"] + list(svc), key="f_service")
    st.session_state.setdefault("f_urgency", "high")
    c3.select_slider("Urgency", c.URGENCIES, key="f_urgency")
    area, lat, lon = _location()
    d1, d2, d3, d4 = st.columns(4)
    d1.checkbox("Needs ventilator", key="f_vent")
    d2.checkbox("ICU bed must be available", key="f_icu")
    d3.checkbox("Emergency department open", key="f_emerg")
    d4.checkbox("Show unsuitable hospitals", value=True, key="f_unsuit")
    e1, e2 = st.columns(2)
    name = e1.text_input("Hospital name contains", key="f_name")
    maxd = e2.slider("Maximum distance (km, 0 = any)", 0, 100, 0, key="f_maxd")

    if st.button("Search hospitals", type="primary"):
        ctx = {"resource_type": c.RESOURCES[st.session_state["f_resource"]], "latitude": lat, "longitude": lon,
               "needs_ventilator": st.session_state["f_vent"], "require_icu": st.session_state["f_icu"],
               "service_id": svc.get(st.session_state["f_service"]), "hospital_name": name or None,
               "max_distance_km": maxd or None, "emergency_only": st.session_state["f_emerg"],
               "include_unsuitable": st.session_state["f_unsuit"], "urgency": st.session_state["f_urgency"]}
        data, err = api.post("/search", json=ctx)
        if c.show_error(err):
            return
        st.session_state["results"], st.session_state["ctx"] = data, ctx
    _results()


def _results():
    results = st.session_state.get("results")
    ctx = st.session_state.get("ctx")
    if results is None:
        return
    suitable = [h for h in results if h["suitable"]]
    if not results:
        st.warning("No hospitals match these filters. Try widening the distance or removing filters.")
        return
    if not suitable:
        st.error("🚫 No capacity available at any hospital for this need. Compare the reasons below "
                 "or relax a filter (for example the ventilator or specialist requirement).")
    else:
        st.subheader(f"{len(suitable)} suitable hospital(s) - best match first")

    pts = pd.DataFrame([{"latitude": h["latitude"], "longitude": h["longitude"],
                         "color": "#2ecc71" if h["suitable"] else "#e74c3c", "size": 140} for h in results]
                       + [{"latitude": ctx["latitude"], "longitude": ctx["longitude"],
                           "color": "#3498db", "size": 220}])
    st.map(pts, latitude="latitude", longitude="longitude", color="color", size="size")
    st.caption("🟢 suitable · 🔴 not suitable · 🔵 your location")

    ref = st.text_input("Patient reference (name or ID) - required to send a referral", key="patient_ref")
    for h in results:
        with st.container(border=True):
            top = st.columns([4, 1.3, 1.3, 1.3, 1.3])
            badge = ("  ⭐ **Best match**" if h["is_best_match"] else "") + \
                    ("  📍 **Nearest suitable**" if h["is_nearest"] else "")
            top[0].markdown(f"**{h['hospital_name']}**{badge}  \n{h['area']} · updated {c.ago(h['last_updated_min_ago'])}")
            if h["suitable"]:
                top[1].metric("Match", f"{h['match_percent']}%")
            else:
                top[1].markdown("**Not suitable**")
            top[2].metric("Distance", f"{h['distance_km']} km")
            top[3].metric("Travel", f"~{h['est_travel_min']} min")
            top[4].metric("Est. wait", f"~{h['est_wait_min']} min")
            st.caption(f"ICU beds: {h['icu_available']} · Ventilators: {h['ventilator_available']} · "
                       f"ED beds: {h['ed_available']} · Requested resource: {h['resource_available']} available"
                       + (f" · Services: {', '.join(h['services'])}" if h["services"] else ""))
            if not h["suitable"]:
                st.caption("❌ " + "; ".join(h["reasons"]))
            if h["possibly_outdated"]:
                st.caption("⚠️ Capacity information may be outdated")
            a, b, _ = st.columns([1, 1.3, 3])
            a.checkbox("Compare", key=f"cmp{h['hospital_id']}")
            if h["suitable"] and b.button("Send referral", key=f"send{h['hospital_id']}", type="primary"):
                _send(h, ctx, ref)
    _compare(results, ctx)


def _send(h, ctx, ref):
    if not ref.strip():
        st.warning("Enter a patient reference first.")
        return
    body = {"patient_reference": ref, "resource_type": ctx["resource_type"], "needs_ventilator": ctx["needs_ventilator"],
            "service_id": ctx["service_id"], "urgency": ctx["urgency"], "latitude": ctx["latitude"],
            "longitude": ctx["longitude"], "location_label": st.session_state.get("f_area"),
            "case_text": st.session_state.get("case_text") or None}
    req, err = api.post("/requests", json=body)
    if c.show_error(err):
        return
    out, err = api.post(f"/requests/{req['request_id']}/send",
                        json={"hospital_id": h["hospital_id"], "match_score": h["match_percent"]})
    if err:
        st.error(f"Request #{req['request_id']}: {err}. Pick another hospital - the request is still open.")
        st.session_state["last_open_request"] = req["request_id"]
        return
    st.success(f"✅ Referral #{req['request_id']} sent to {h['hospital_name']}. A bed is held for you until "
               f"{c.fmt_time(out['reserved_until'])}. Follow it under **My requests**.")


def _compare(results, ctx):
    chosen = [h for h in results if st.session_state.get(f"cmp{h['hospital_id']}")]
    if len(chosen) < 2:
        return
    st.subheader("Hospital comparison")
    if len(chosen) > 4:
        st.warning("Select at most 4 hospitals to compare.")
        return
    data, err = api.post("/compare", json={"hospital_ids": [h["hospital_id"] for h in chosen],
                                           "latitude": ctx["latitude"], "longitude": ctx["longitude"]})
    if c.show_error(err):
        return
    rows = {"Distance": {}, "Travel time": {}, "Est. wait": {}, "Emergency dept": {}, "Services": {}}
    resources = sorted({cap["resource_type"] for d in data for cap in d["capacity"]})
    for res in resources:
        rows[f"{c.res_label(res)} (free / total)"] = {}
    for d in data:
        n = d["hospital_name"]
        rows["Distance"][n] = f"{d['distance_km']} km"
        rows["Travel time"][n] = f"~{d['est_travel_min']} min"
        rows["Est. wait"][n] = f"~{d['est_wait_min']} min"
        rows["Emergency dept"][n] = "Open" if d["emergency_available"] else "Closed"
        rows["Services"][n] = ", ".join(d["services"]) or "-"
        caps = {cap["resource_type"]: cap for cap in d["capacity"]}
        for res in resources:
            cap = caps.get(res)
            rows[f"{c.res_label(res)} (free / total)"][n] = f"{cap['available']} / {cap['total_capacity']}" if cap else "-"
    st.dataframe(pd.DataFrame(rows).T)
    for d in data:
        for res, o in d["outlook"].items():
            p = o["prediction"]
            if p["status"] in ("limited_now", "limited_soon"):
                when = "now" if p["status"] == "limited_now" else f"in about {p['hours']} h"
                st.caption(f"⚠️ {d['hospital_name']}: {c.res_label(res)} capacity may become limited {when}.")

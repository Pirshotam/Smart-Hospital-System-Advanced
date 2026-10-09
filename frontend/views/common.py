"""Shared helpers: formatting, constants, notifications."""
import os
from datetime import datetime, timedelta, timezone

import streamlit as st

import api

LOCAL_TZ_HOURS = int(os.getenv("LOCAL_TZ_HOURS", "5"))      # Pakistan = UTC+5

RESOURCES = {"ICU bed": "icu_bed", "Emergency bed": "emergency_bed", "General bed": "general_bed",
             "NICU bed": "nicu_bed", "Isolation bed": "isolation_bed", "Operation theatre": "operation_theatre",
             "Trauma capacity": "trauma", "Dialysis": "dialysis", "Ventilator": "ventilator",
             "Ambulance": "ambulance"}
RESOURCE_LABEL = {v: k for k, v in RESOURCES.items()}
LOCATIONS = {"Latifabad, Hyderabad": (25.3797, 68.3547), "Qasimabad, Hyderabad": (25.4167, 68.3333),
             "Hirabad, Hyderabad": (25.3920, 68.3700), "Saddar, Hyderabad": (25.3850, 68.3650),
             "Jamshoro": (25.4290, 68.2810), "Kotri": (25.3660, 68.3080)}
STEPS = ["searching", "request_sent", "hospital_reviewing", "accepted", "patient_transferred", "admitted"]
STATUS_TEXT = {"searching": "Searching", "request_sent": "Request sent", "hospital_reviewing": "Hospital reviewing",
               "accepted": "Accepted", "patient_transferred": "Patient transferred", "admitted": "Admitted",
               "rejected": "Rejected", "cancelled": "Cancelled", "expired": "Expired",
               "no_capacity": "No capacity available"}
URGENCIES = ["low", "medium", "high", "critical"]


def app_url():
    """Public address of this app (used inside the QR code). Set APP_URL to force a value."""
    forced = os.getenv("APP_URL")
    if forced:
        return forced.rstrip("/")
    try:
        url = getattr(st.context, "url", None)
        if url:
            return str(url).split("?")[0].rstrip("/")
        headers = st.context.headers
        host = headers.get("X-Forwarded-Host") or headers.get("Host")
        if host:
            scheme = "http" if host.startswith(("localhost", "127.", "0.0.0.0")) else "https"
            return f"{scheme}://{host}"
    except Exception:
        pass
    return "http://localhost:8501"


def res_label(value):
    return RESOURCE_LABEL.get(value, str(value).replace("_", " ").title())


def status_text(value):
    return STATUS_TEXT.get(value, value)


def parse_utc(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "")[:19])


def fmt_time(value):
    """UTC timestamp from the API -> local time text."""
    t = parse_utc(value)
    return (t + timedelta(hours=LOCAL_TZ_HOURS)).strftime("%d %b %H:%M") if t else "-"


def ago(minutes):
    if minutes is None:
        return "never"
    minutes = int(minutes)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    return f"{minutes // 60} h {minutes % 60} min ago"


def minutes_left(value):
    t = parse_utc(value)
    if not t:
        return None
    return max(0, int((t - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() // 60))


def show_error(err):
    if err:
        st.error(err)
        return True
    return False


def notifications_sidebar():
    data, err = api.get("/notifications")
    if err or data is None:
        return
    unread = data["unread"]
    with st.sidebar.expander(f"🔔 Notifications ({unread} new)", expanded=bool(unread)):
        if not data["items"]:
            st.caption("Nothing yet.")
        for n in data["items"][:8]:
            dot = "🔵" if not n["is_read"] else "⚪"
            st.markdown(f"{dot} **{n['title']}**  \n{n['message']}  \n:gray[{fmt_time(n['created_at'])}]")
        if unread and st.button("Mark all as read", key="notif_read_all"):
            api.post("/notifications/read-all")
            st.rerun()

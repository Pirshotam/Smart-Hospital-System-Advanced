"""Chart panels shared by the hospital dashboard and the admin pages."""
import pandas as pd
import streamlit as st

from views import common as c

PREDICTION_TEXT = {"limited_now": "🔴 Capacity is already limited",
                   "limited_soon": "🟠 May become limited in about {hours} hours",
                   "stable": "🟢 Stable", "improving": "🟢 Improving (occupancy falling)"}


def outlook_panel(outlook, key="fc"):
    """Capacity prediction + 24-hour capacity forecast."""
    if not outlook:
        st.info("No ICU / emergency / general bed data to forecast yet.")
        return
    st.subheader("Capacity prediction")
    cols = st.columns(len(outlook))
    for col, (res, o) in zip(cols, outlook.items()):
        p = o["prediction"]
        col.metric(c.res_label(res), f"{o['current']['available']} free of {o['current']['total']}")
        col.caption(PREDICTION_TEXT[p["status"]].format(hours=p["hours"]))
    st.subheader("Capacity forecast - next 24 hours")
    res = st.selectbox("Resource", list(outlook), format_func=c.res_label, key=f"{key}_res")
    fc = pd.DataFrame(outlook[res]["forecast"])
    fc["hour"] = fc["hour_local"].map(lambda h: f"{h:02d}:00")
    st.line_chart(fc.set_index("hour")["occupancy_pct"], y_label="Expected occupancy %")
    peak = fc.loc[fc["occupancy_pct"].idxmax()]
    st.caption(f"Expected busiest hour: **{peak['hour']}** at about {peak['occupancy_pct']}% occupancy. "
               "The forecast applies the typical daily pattern of the last 14 days to today's level.")


def trends_panel(rows):
    """Capacity trends by date."""
    if not rows:
        st.info("No snapshot history yet.")
        return
    df = pd.DataFrame(rows)
    df["resource"] = df["resource_type"].map(c.res_label)
    pivot = df.pivot_table(index="day", columns="resource", values="occupancy_pct")
    st.line_chart(pivot, y_label="Average occupancy %")


def demand_panel(d):
    """Demand forecasting: busy hours, services and peak emergency periods."""
    if not d:
        return
    st.caption(f"Based on the last {d['days_of_data']} day(s) of referral requests.")
    if d["peak_emergency_periods"]:
        st.success("🚨 Peak emergency demand: " + ", ".join(d["peak_emergency_periods"]))
    by_hour = pd.DataFrame(d["by_hour"]).set_index("label")
    st.subheader("Requests by hour of day")
    st.bar_chart(by_hour[["avg_requests", "avg_emergency"]])
    a, b = st.columns(2)
    with a:
        st.subheader("Most requested services")
        if d["top_services"]:
            st.bar_chart(pd.DataFrame(d["top_services"]).set_index("name")["requests"])
        st.subheader("Most requested facilities")
        if d["top_resources"]:
            df = pd.DataFrame(d["top_resources"])
            df["name"] = df["name"].map(c.res_label)
            st.bar_chart(df.set_index("name")["requests"])
    with b:
        st.subheader("Requests by weekday")
        if d["by_weekday"]:
            st.bar_chart(pd.DataFrame(d["by_weekday"]).set_index("day")["requests"])
        st.subheader("Expected requests - next 24 hours")
        st.line_chart(pd.DataFrame(d["next_24h"]).set_index("label")["expected_requests"])

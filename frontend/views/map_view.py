"""Live hospital map and regional capacity heatmap."""
import pandas as pd
import streamlit as st

import api
from views import common as c

COLORS = {"green": "#2ecc71", "amber": "#f39c12", "red": "#e74c3c"}


@st.fragment(run_every=15)
def _live():
    rows, err = api.get("/map/hospitals")
    if c.show_error(err) or not rows:
        return
    df = pd.DataFrame(rows)
    df["color"] = df["status"].map(COLORS)
    df["size"] = 160
    st.map(df, latitude="latitude", longitude="longitude", color="color", size="size")
    st.caption("🟢 3+ ICU beds free · 🟠 1-2 ICU beds free · 🔴 no ICU bed free. Refreshes every 15 seconds.")
    table = df[["hospital_name", "area", "icu_available", "general_available", "ed_available", "vent_available",
                "minutes_ago"]].copy()
    table.columns = ["Hospital", "Area", "ICU free", "General free", "ED free", "Ventilators free", "Updated (min ago)"]
    st.dataframe(table.fillna(0), hide_index=True)


def live_map():
    st.header("🗺️ Live hospital map")
    _live()


def heatmap():
    st.header("🔥 Regional capacity heatmap")
    pts, err = api.get("/analytics/heatmap")
    if c.show_error(err) or not pts:
        return
    df = pd.DataFrame(pts)
    st.caption("Brighter / redder = fuller hospitals. Based on general, emergency, ICU, NICU and isolation beds.")
    try:
        import pydeck as pdk
        df["color"] = df["occupancy_pct"].apply(
            lambda p: [int(min(255, 2.55 * p)), int(max(0, 255 - 2.55 * p)), 60, 190])
        layers = [
            pdk.Layer("HeatmapLayer", data=df, get_position="[longitude, latitude]",
                      get_weight="occupancy_pct", radiusPixels=90, opacity=0.6),
            pdk.Layer("ScatterplotLayer", data=df, get_position="[longitude, latitude]",
                      get_fill_color="color", get_radius=350, pickable=True)]
        view = pdk.ViewState(latitude=float(df["latitude"].mean()), longitude=float(df["longitude"].mean()), zoom=10)
        st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=view,
                                 tooltip={"text": "{hospital_name}\nOccupancy: {occupancy_pct}%\nFree beds: {available}"}))
    except Exception:
        df["c"] = df["occupancy_pct"].apply(lambda p: "#e74c3c" if p >= 85 else ("#f39c12" if p >= 70 else "#2ecc71"))
        df["s"] = 200
        st.map(df, latitude="latitude", longitude="longitude", color="c", size="s")
    area = df.groupby("area").agg(hospitals=("hospital_id", "count"), free_beds=("available", "sum"),
                                  total_beds=("total", "sum")).reset_index()
    area["free_%"] = (100 * area["free_beds"] / area["total_beds"]).round(1)
    st.subheader("Areas with the lowest free capacity")
    st.dataframe(area.sort_values("free_%"), hide_index=True)

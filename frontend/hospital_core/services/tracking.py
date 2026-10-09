"""Simulated ambulance tracking: the unit moves in a straight line from the patient's location
to the hospital over the estimated travel time (SIM_SPEED speeds up demos)."""
from hospital_core.config import SIM_SPEED
from hospital_core.db import parse, utc_now


def tracking_state(req, hospital):
    if not req["dispatched_at"] or req["latitude"] is None:
        return {"dispatched": False}
    total = max(req["transfer_eta_min"] or 1, 1)
    elapsed = (utc_now() - parse(req["dispatched_at"])).total_seconds() / 60 * SIM_SPEED
    progress = min(1.0, elapsed / total)
    lat = req["latitude"] + (hospital["latitude"] - req["latitude"]) * progress
    lon = req["longitude"] + (hospital["longitude"] - req["longitude"]) * progress
    return {"dispatched": True, "ambulance_unit": req["ambulance_unit"],
            "latitude": lat, "longitude": lon, "progress": round(progress, 3),
            "eta_remaining_min": round(max(0.0, total - elapsed), 1), "arrived": progress >= 1.0,
            "origin": {"latitude": req["latitude"], "longitude": req["longitude"]},
            "destination": {"latitude": hospital["latitude"], "longitude": hospital["longitude"],
                            "name": hospital["hospital_name"]}}

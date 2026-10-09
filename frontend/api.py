"""The 'API' used by every page: api.get / api.post / api.patch / api.put.

Earlier versions sent these calls to a FastAPI server. Now they run inside the Streamlit process
(hospital_core.router), so the app needs no separate backend or database server - it is one
Streamlit app with an embedded SQLite database that creates and fills itself on first start.

Streamlit keeps each visitor's session on the server, so who is logged in cannot be forged from
the browser. Every call is checked against the user's role in hospital_core.router."""
import traceback
from datetime import timedelta

import streamlit as st

from hospital_core import router
from hospital_core.config import SESSION_HOURS
from hospital_core.db import utc_now
from hospital_core.router import ApiError
import hospital_core.handlers  # noqa: F401  (registers all routes)


def logout(message=None):
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    if message:
        st.session_state["flash"] = message


def _ip():
    try:
        forwarded = st.context.headers.get("X-Forwarded-For", "")
        return forwarded.split(",")[0].strip() or None
    except Exception:
        return None


def call(method, path, auth=True, json=None, params=None, **_unused):
    """Returns (data, error_message), like the old HTTP client did."""
    user_id = None
    if auth:
        user = st.session_state.get("user")
        if user and st.session_state.get("token"):
            user_id = user["user_id"]
        login_at = st.session_state.get("login_at")
        if login_at and utc_now() - login_at > timedelta(hours=SESSION_HOURS):
            logout("Your session expired. Please log in again.")
            st.rerun()
    try:
        data = router.dispatch(method, path, user_id=user_id, body=json, params=params, ip=_ip())
    except ApiError as e:
        if e.status == 401 and auth:
            logout("Your session ended. Please log in again.")
            st.rerun()
        return None, e.detail
    except Exception as e:                      # a bug or a database problem: show it, do not crash the page
        print(traceback.format_exc())
        return None, f"Something went wrong ({type(e).__name__}: {e}). Please try again."
    if method == "POST" and path == "/auth/login":
        st.session_state["login_at"] = utc_now()
    return data, None


def get(path, **kw):
    return call("GET", path, **kw)


def post(path, **kw):
    return call("POST", path, **kw)


def patch(path, **kw):
    return call("PATCH", path, **kw)


def put(path, **kw):
    return call("PUT", path, **kw)

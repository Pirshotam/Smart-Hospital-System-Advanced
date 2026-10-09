"""Small input checks (the FastAPI version used pydantic for this)."""
import re

from hospital_core.router import ApiError
from hospital_core.schema import RESOURCES

URGENCIES = ("low", "medium", "high", "critical")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def text(body, key, min_len=0, max_len=255, required=True, default=None):
    v = body.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise ApiError(422, f"{key.replace('_', ' ').capitalize()} is required")
        return default
    v = str(v).strip()
    if len(v) < min_len or len(v) > max_len:
        raise ApiError(422, f"{key.replace('_', ' ').capitalize()} must be {min_len}-{max_len} characters")
    return v


def number(body, key, lo=None, hi=None, required=True, default=None, integer=False):
    v = body.get(key)
    if v is None or v == "":
        if required:
            raise ApiError(422, f"{key.replace('_', ' ').capitalize()} is required")
        return default
    try:
        v = int(v) if integer else float(v)
    except (TypeError, ValueError):
        raise ApiError(422, f"{key.replace('_', ' ').capitalize()} must be a number")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ApiError(422, f"{key.replace('_', ' ').capitalize()} must be between {lo} and {hi}")
    return v


def choice(body, key, options, default=None):
    v = body.get(key, default)
    if v not in options:
        raise ApiError(422, f"{key.replace('_', ' ').capitalize()} must be one of: {', '.join(options)}")
    return v


def flag(body, key, default=False):
    return bool(body.get(key, default))


def email(value):
    v = (value or "").strip().lower()
    if not _EMAIL.match(v) or len(v) > 255:
        raise ApiError(422, "Enter a valid email address")
    return v


def resource(body, key="resource_type"):
    return choice(body, key, RESOURCES)


def urgency(body, key="urgency", default="medium"):
    return choice(body, key, URGENCIES, default)

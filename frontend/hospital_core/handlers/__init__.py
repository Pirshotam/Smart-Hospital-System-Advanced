"""Importing this package registers every route with hospital_core.router."""
from hospital_core.handlers import (admin, ai, analytics, auth, hospital, notifications,  # noqa: F401
                                    referrals, search)

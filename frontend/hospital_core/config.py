"""Settings. Every value can be overridden with an environment variable."""
import os

SESSION_HOURS = int(os.getenv("SESSION_HOURS", "8"))           # automatic logout
MAX_FAILED_LOGINS = int(os.getenv("MAX_FAILED_LOGINS", "5"))
LOCK_MINUTES = int(os.getenv("LOCK_MINUTES", "10"))

PENDING_HOLD_MIN = int(os.getenv("PENDING_HOLD_MIN", "15"))    # bed held while the hospital reviews
ACCEPT_HOLD_MIN = int(os.getenv("ACCEPT_HOLD_MIN", "15"))      # hold restarts on acceptance
STALE_MINUTES = int(os.getenv("STALE_MINUTES", "60"))          # older capacity data is flagged
SNAPSHOT_INTERVAL_MIN = int(os.getenv("SNAPSHOT_INTERVAL_MIN", "60"))

AVG_SPEED_KMH = float(os.getenv("AVG_SPEED_KMH", "40"))
SIM_SPEED = float(os.getenv("SIM_SPEED", "1"))                 # >1 speeds up the ambulance simulation
LOCAL_TZ_HOURS = int(os.getenv("LOCAL_TZ_HOURS", "5"))         # Pakistan = UTC+5

BED_TYPES = ("general_bed", "emergency_bed", "icu_bed", "nicu_bed", "isolation_bed")
DEMO_PASSWORD = "Demo@1234"

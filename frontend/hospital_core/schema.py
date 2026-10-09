"""SQLite schema. Bump SCHEMA_VERSION when you change it - the demo database is then rebuilt."""
SCHEMA_VERSION = 2   # 2: demo admitted patients now have a status history

RESOURCES = ("general_bed", "emergency_bed", "icu_bed", "nicu_bed", "ventilator",
             "operation_theatre", "isolation_bed", "dialysis", "trauma", "ambulance")
STATUSES = ("searching", "request_sent", "hospital_reviewing", "accepted", "patient_transferred",
            "admitted", "rejected", "cancelled", "expired", "no_capacity")


def _in(values):
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


SCHEMA = f"""
CREATE TABLE hospitals (
  hospital_id         INTEGER PRIMARY KEY AUTOINCREMENT,
  hospital_name       TEXT NOT NULL,
  area                TEXT NOT NULL,
  city                TEXT NOT NULL DEFAULT 'Hyderabad',
  location            TEXT NOT NULL,
  latitude            REAL NOT NULL,
  longitude           REAL NOT NULL,
  contact             TEXT,
  verification_status TEXT NOT NULL DEFAULT 'pending' CHECK (verification_status IN ('pending','verified','rejected')),
  emergency_available INTEGER NOT NULL DEFAULT 1,
  account_status      TEXT NOT NULL DEFAULT 'active' CHECK (account_status IN ('active','inactive','suspended')),
  verified_by         INTEGER,
  verified_at         TEXT,
  created_at          TEXT NOT NULL
);

CREATE TABLE users (
  user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  full_name     TEXT NOT NULL,
  email         TEXT NOT NULL UNIQUE,
  phone         TEXT,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('patient','coordinator','hospital_staff','admin')),
  hospital_id   INTEGER REFERENCES hospitals(hospital_id),
  status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','suspended')),
  failed_logins INTEGER NOT NULL DEFAULT 0,
  locked_until  TEXT,
  last_login    TEXT,
  approved_by   INTEGER,
  approved_at   TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE login_log (
  log_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  email      TEXT NOT NULL,
  user_id    INTEGER,
  success    INTEGER NOT NULL,
  reason     TEXT,
  ip         TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE services (
  service_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  service_name TEXT NOT NULL UNIQUE
);

CREATE TABLE hospital_services (
  hospital_id  INTEGER NOT NULL REFERENCES hospitals(hospital_id) ON DELETE CASCADE,
  service_id   INTEGER NOT NULL REFERENCES services(service_id) ON DELETE CASCADE,
  is_available INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (hospital_id, service_id)
);

CREATE TABLE capacity (
  capacity_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  hospital_id    INTEGER NOT NULL REFERENCES hospitals(hospital_id) ON DELETE CASCADE,
  resource_type  TEXT NOT NULL CHECK (resource_type IN {_in(RESOURCES)}),
  total_capacity INTEGER NOT NULL,
  occupied       INTEGER NOT NULL DEFAULT 0,
  reserved       INTEGER NOT NULL DEFAULT 0,
  unavailable    INTEGER NOT NULL DEFAULT 0,
  available      INTEGER GENERATED ALWAYS AS (total_capacity - occupied - reserved - unavailable) VIRTUAL,
  last_updated   TEXT NOT NULL,
  UNIQUE (hospital_id, resource_type),
  CHECK (total_capacity >= 0 AND occupied >= 0 AND reserved >= 0 AND unavailable >= 0),
  CHECK (occupied + reserved + unavailable <= total_capacity)
);

CREATE TABLE referral_requests (
  request_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  request_code      TEXT NOT NULL UNIQUE,
  patient_reference TEXT NOT NULL,
  created_by        INTEGER NOT NULL REFERENCES users(user_id),
  required_resource TEXT NOT NULL CHECK (required_resource IN {_in(RESOURCES)}),
  needs_ventilator  INTEGER NOT NULL DEFAULT 0,
  required_service  INTEGER REFERENCES services(service_id),
  location          TEXT,
  latitude          REAL,
  longitude         REAL,
  urgency           TEXT NOT NULL DEFAULT 'medium' CHECK (urgency IN ('low','medium','high','critical')),
  case_text         TEXT,
  case_summary      TEXT,
  selected_hospital INTEGER REFERENCES hospitals(hospital_id),
  match_score       REAL,
  request_status    TEXT NOT NULL DEFAULT 'searching' CHECK (request_status IN {_in(STATUSES)}),
  rejection_reason  TEXT,
  ambulance_unit    TEXT,
  dispatched_at     TEXT,
  transfer_eta_min  INTEGER,
  created_at        TEXT NOT NULL,
  accepted_at       TEXT,
  responded_at      TEXT,
  transferred_at    TEXT,
  admitted_at       TEXT,
  discharged_at     TEXT
);
CREATE INDEX idx_req_status ON referral_requests(request_status);
CREATE INDEX idx_req_hospital ON referral_requests(selected_hospital, request_status);
CREATE INDEX idx_req_creator ON referral_requests(created_by);
CREATE INDEX idx_req_created ON referral_requests(created_at);

CREATE TABLE reservations (
  reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id     INTEGER NOT NULL REFERENCES referral_requests(request_id) ON DELETE CASCADE,
  capacity_id    INTEGER NOT NULL REFERENCES capacity(capacity_id),
  units          INTEGER NOT NULL DEFAULT 1 CHECK (units > 0),
  state          TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','confirmed','expired','released')),
  reserved_at    TEXT NOT NULL,
  expires_at     TEXT NOT NULL
);
CREATE INDEX idx_res_active ON reservations(state, expires_at);
CREATE INDEX idx_res_request ON reservations(request_id, state);

CREATE TABLE request_status_history (
  history_id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id INTEGER NOT NULL REFERENCES referral_requests(request_id) ON DELETE CASCADE,
  status     TEXT NOT NULL,
  note       TEXT,
  changed_by INTEGER,
  changed_at TEXT NOT NULL
);

CREATE TABLE capacity_audit_log (
  log_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  capacity_id   INTEGER NOT NULL REFERENCES capacity(capacity_id),
  changed_by    INTEGER,
  old_total     INTEGER, new_total INTEGER,
  old_occupied  INTEGER, new_occupied INTEGER,
  reason        TEXT,
  flagged       INTEGER NOT NULL DEFAULT 0,
  reviewed      INTEGER NOT NULL DEFAULT 0,
  reviewed_by   INTEGER,
  reviewed_at   TEXT,
  review_action TEXT,
  changed_at    TEXT NOT NULL
);

CREATE TABLE capacity_snapshots (
  snapshot_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  hospital_id    INTEGER NOT NULL REFERENCES hospitals(hospital_id) ON DELETE CASCADE,
  resource_type  TEXT NOT NULL,
  occupied       INTEGER NOT NULL,
  total_capacity INTEGER NOT NULL,
  captured_at    TEXT NOT NULL
);
CREATE INDEX idx_snap ON capacity_snapshots(hospital_id, resource_type, captured_at);
CREATE INDEX idx_snap_time ON capacity_snapshots(captured_at);

CREATE TABLE notifications (
  notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id         INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  title           TEXT NOT NULL,
  message         TEXT NOT NULL,
  kind            TEXT NOT NULL DEFAULT 'info',
  request_id      INTEGER,
  is_read         INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL
);
CREATE INDEX idx_notif_user ON notifications(user_id, is_read, created_at);
"""

"""SQLite database: connection, transactions and automatic first-run setup.

* Writers use BEGIN IMMEDIATE, so only one write transaction runs at a time. That is what
  prevents two people from being assigned the same last bed (the MySQL version used row locks).
* The database is created and filled with demo data automatically the first time the app runs.
* Where the file lives: HOSPITAL_DB_PATH if set, else  frontend/data/  if writable, else the
  system temp folder. On Streamlit Community Cloud the file is lost when the app restarts.
"""
import os
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hospital_core.schema import SCHEMA, SCHEMA_VERSION

FMT = "%Y-%m-%d %H:%M:%S"


def utc_now():
    """Naive UTC time, whole seconds."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def ts(dt):
    return dt.strftime(FMT) if dt else None


def parse(value):
    if not value:
        return None
    return datetime.strptime(str(value).replace("T", " ")[:19], FMT)


def minutes_between(older, newer=None):
    """Whole minutes from `older` (text or datetime) to `newer` (default: now)."""
    older = parse(older) if isinstance(older, str) else older
    newer = newer or utc_now()
    return int((newer - older).total_seconds() // 60)


def ago(minutes):
    return utc_now() - timedelta(minutes=minutes)


def _db_path():
    env = os.getenv("HOSPITAL_DB_PATH")
    if env:
        return Path(env)
    for base in (Path(__file__).resolve().parents[1] / "data", Path(tempfile.gettempdir())):
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".write_test"
            probe.write_text("x")
            probe.unlink()
            return base / "smart_hospital.db"
        except OSError:
            continue
    return Path("smart_hospital.db")


DB_PATH = _db_path()


def _dict_factory(cursor, row):
    return {d[0]: v for d, v in zip(cursor.description, row)}


def _connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


class Conn:
    """Small helper around a sqlite3 connection: rows come back as dicts."""

    def __init__(self, conn):
        self.conn = conn

    def run(self, sql, params=()):
        return self.conn.execute(sql, params)

    def all(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()

    def insert(self, sql, params=()):
        return self.conn.execute(sql, params).lastrowid

    def many(self, sql, rows):
        self.conn.executemany(sql, rows)


@contextmanager
def read():
    """Read-only access (no lock taken)."""
    conn = _connect()
    try:
        yield Conn(conn)
    finally:
        conn.close()


@contextmanager
def tx():
    """One write transaction: commits on success, rolls everything back on any error."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield Conn(conn)
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------- first-run setup
_lock = threading.Lock()
_ready = False


def is_ready():
    return _ready


def _remove_files():
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(DB_PATH) + suffix).unlink()
        except FileNotFoundError:
            pass


def _build():
    from hospital_core import seed
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for statement in _split(SCHEMA):
            conn.execute(statement)
        seed.run(Conn(conn))
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def _split(script):
    return [s.strip() for s in script.split(";") if s.strip()]


def ensure_ready():
    """Create + seed the database if needed (runs once per server process)."""
    global _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        needs_build = True
        if DB_PATH.exists():
            conn = _connect()
            try:
                version = conn.execute("PRAGMA user_version").fetchone()["user_version"]
                has_hospitals = conn.execute(
                    "SELECT COUNT(*) AS n FROM sqlite_master WHERE name = 'hospitals'").fetchone()["n"]
                needs_build = version != SCHEMA_VERSION or not has_hospitals
            finally:
                conn.close()
        if needs_build:
            _remove_files()
            _build()
        _ready = True


def reset_database():
    """Wipe everything and load the demo data again."""
    global _ready
    with _lock:
        _ready = False
        _remove_files()
        _build()
        _ready = True

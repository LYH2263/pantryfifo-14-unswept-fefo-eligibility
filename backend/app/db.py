import os, sqlite3
from pathlib import Path

BUSY_TIMEOUT_MS = 5000


def db_path() -> Path:
    d = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "pantryfifo.db"


def connect():
    c = sqlite3.connect(db_path(), timeout=BUSY_TIMEOUT_MS / 1000)
    c.row_factory = sqlite3.Row
    # WAL lets the sweep's BEGIN IMMEDIATE coexist with readers; busy_timeout
    # makes a contending writer wait (and retry) instead of failing instantly.
    c.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    c.execute("PRAGMA journal_mode=WAL")
    return c

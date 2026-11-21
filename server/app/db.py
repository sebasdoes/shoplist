"""T008 — SQLite (WAL) helpers, schema v1 migration runner, global rev sequence."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    quantity TEXT,
    note TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'dropped')),
    staple INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    rev INTEGER NOT NULL,
    tombstone INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_active_name_key
    ON items(name_key) WHERE status = 'active' AND tombstone = 0;
CREATE INDEX IF NOT EXISTS idx_items_rev ON items(rev);

CREATE TABLE IF NOT EXISTS history (
    id TEXT PRIMARY KEY,
    item_id TEXT,
    name TEXT NOT NULL,
    quantity TEXT,
    note TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('bought', 'not_bought')),
    staple_snapshot INTEGER NOT NULL DEFAULT 0,
    actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    rev INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_occurred_at ON history(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_rev ON history(rev);

CREATE TABLE IF NOT EXISTS trips (
    id TEXT PRIMARY KEY,
    completed_by TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    rev INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    subject TEXT PRIMARY KEY,
    display_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_meta (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
INSERT OR IGNORE INTO sync_meta(key, value) VALUES ('rev', 0);
"""


def stamp() -> str:
    """Server-stamped ISO-8601 UTC timestamp (client clocks are not trusted)."""
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def connect(path: str) -> sqlite3.Connection:
    parent = Path(path).parent
    if str(parent):
        parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    current = row[0] or 0
    if current >= SCHEMA_VERSION:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement in _SCHEMA_V1.strip().split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, stamp()),
        )
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def next_rev(conn: sqlite3.Connection) -> int:
    """Bump and return the global monotonic sync revision."""
    conn.execute("UPDATE sync_meta SET value = value + 1 WHERE key = 'rev'")
    return conn.execute("SELECT value FROM sync_meta WHERE key = 'rev'").fetchone()[0]


def current_rev(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM sync_meta WHERE key = 'rev'").fetchone()
    return row[0] if row else 0


def upsert_user(conn: sqlite3.Connection, subject: str, display_name: str | None) -> None:
    """users: set/refreshed when first-seen/seen in a token (data-model users)."""
    if not subject:
        return
    if display_name:
        conn.execute(
            "INSERT INTO users(subject, display_name) VALUES (?, ?) "
            "ON CONFLICT(subject) DO UPDATE SET display_name = excluded.display_name",
            (subject, display_name),
        )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO users(subject, display_name) VALUES (?, ?)", (subject, "")
        )

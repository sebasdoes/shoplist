"""T002 — SQLite (WAL) helpers, schema v1→v2 migration runner, global rev sequence.

Schema v2 (specs/002 data-model.md): adds `lists`, seeds the default list
`Boodschappen` (fixed uuid), backfills items/trips/history `list_id`, and
swaps the partial unique index to (list_id, name_key). Gated by
PRAGMA user_version; idempotent; the rev sequence is untouched.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2

DEFAULT_LIST_ID = "00000000-0000-4000-8000-000000000001"
DEFAULT_LIST_NAME = "Boodschappen"

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

_SCHEMA_V2_ADDITIONS = """
CREATE TABLE IF NOT EXISTS lists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_by TEXT,
    created_at TEXT NOT NULL,
    tombstone INTEGER NOT NULL DEFAULT 0,
    rev INTEGER NOT NULL
);
"""

_MIGRATE_V1_TO_V2_STATEMENTS = (
    f"ALTER TABLE items ADD COLUMN list_id TEXT NOT NULL DEFAULT '{DEFAULT_LIST_ID}'",
    "DROP INDEX IF EXISTS idx_items_active_name_key",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_items_active_list_name_key "
    "ON items(list_id, name_key) WHERE status = 'active' AND tombstone = 0",
    f"ALTER TABLE trips ADD COLUMN list_id TEXT NOT NULL DEFAULT '{DEFAULT_LIST_ID}'",
    f"ALTER TABLE history ADD COLUMN list_id TEXT NOT NULL DEFAULT '{DEFAULT_LIST_ID}'",
    # The seeded list needs a rev above every existing data rev, otherwise
    # devices whose watermark is already at the v1 max would never pull it.
    "UPDATE sync_meta SET value = max(COALESCE(value, 0), "
    "COALESCE((SELECT MAX(rev) FROM items), 0), COALESCE((SELECT MAX(rev) FROM trips), 0), "
    "COALESCE((SELECT MAX(rev) FROM history), 0)) WHERE key = 'rev'",
)

# Full v2 DDL for fresh installs: v1 tables + list_id columns + lists.
_SCHEMA_V2 = (
    _SCHEMA_V1
    + _SCHEMA_V2_ADDITIONS
    + "\n".join(statement + ";" for statement in _MIGRATE_V1_TO_V2_STATEMENTS)
)


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


def _run_script(conn: sqlite3.Connection, script: str) -> None:
    """Execute a DDL script statement-by-statement (keeps the open transaction)."""
    for statement in script.strip().split(";"):
        if statement.strip():
            conn.execute(statement)


def _seed_default_list(conn: sqlite3.Connection) -> None:
    """Seed Boodschappen with the fixed uuid; rev comes off the global sequence so
    devices that pulled earlier receive it in their watermark delta."""
    conn.execute(
        "INSERT OR IGNORE INTO lists (id, name, created_by, created_at, tombstone, rev) "
        "VALUES (?, ?, NULL, ?, 0, ?)",
        (DEFAULT_LIST_ID, DEFAULT_LIST_NAME, stamp(), next_rev(conn)),
    )


def _current_schema_version(conn: sqlite3.Connection) -> int:
    """user_version is the gate; schema_migrations covers pre-v2 databases that
    never recorded a user_version (v1 shipped with user_version=0)."""
    uv = conn.execute("PRAGMA user_version").fetchone()[0]
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return max(uv, row[0] or 0)


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    current = _current_schema_version(conn)
    if current >= SCHEMA_VERSION:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if current == 0:
            # Fresh install: build the full v2 schema directly, then seed.
            _run_script(conn, _SCHEMA_V2)
        else:
            # v1 → v2 one-shot migration, backfills default to the seeded list.
            _run_script(conn, _SCHEMA_V2_ADDITIONS)
            for statement in _MIGRATE_V1_TO_V2_STATEMENTS:
                conn.execute(statement)
        _seed_default_list(conn)
        conn.execute("PRAGMA user_version = 2")
        conn.execute(
            "INSERT OR REPLACE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
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

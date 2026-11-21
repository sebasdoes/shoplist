"""T001 (written FIRST, TDD) — migration v1→v2 per specs/002 data-model.md.

Creates `lists`, seeds `Boodschappen` (fixed uuid 00000000-0000-4000-8000-
000000000001), backfills items/trips/history `list_id`, replaces the unique
index with `(list_id, name_key)` on active+live rows, sets user_version=2,
and is idempotent. Fresh installs build the v2 schema directly.
"""

import sqlite3
import uuid

from app import db

DEFAULT_LIST_ID = "00000000-0000-4000-8000-000000000001"

V1_ITEM_COLS = (
    "id, name, name_key, quantity, note, status, staple, created_by, "
    "created_at, updated_at, rev, tombstone"
)


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def make_v1_db(path) -> sqlite3.Connection:
    """Build a literal schema-v1 database with data, as feature 001 shipped it."""
    conn = sqlite3.connect(path)
    conn.executescript(db._SCHEMA_V1)
    conn.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (1, '2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        f"INSERT INTO items ({V1_ITEM_COLS}) VALUES "
        "('11111111-1111-4111-8111-111111111111', 'melk', 'melk', '1x', NULL, "
        "'active', 0, 'user-a', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 1, 0)"
    )
    conn.execute(
        f"INSERT INTO items ({V1_ITEM_COLS}) VALUES "
        "('22222222-2222-4222-8222-222222222222', 'brood', 'brood', NULL, NULL, "
        "'completed', 0, 'user-a', '2026-01-01T00:00:00+00:00', "
        "'2026-01-01T00:00:00+00:00', 2, 0)"
    )
    conn.execute(
        f"INSERT INTO items ({V1_ITEM_COLS}) VALUES "
        "('33333333-3333-4333-8333-333333333333', 'spruitjes', 'spruitjes', NULL, 'yuck', "
        "'active', 0, 'user-a', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 3, 1)"
    )
    conn.execute(
        "INSERT INTO history (id, item_id, name, quantity, note, outcome, staple_snapshot, "
        "actor, occurred_at, rev) VALUES "
        "('44444444-4444-4444-8444-444444444444', NULL, 'koffie', '2x', NULL, 'bought', 0, "
        "'user-a', '2026-01-01T00:00:01+00:00', 4)"
    )
    conn.execute(
        "INSERT INTO trips (id, completed_by, completed_at, rev) VALUES "
        "('55555555-5555-4555-8555-555555555555', 'user-a', '2026-01-01T00:00:02+00:00', 5)"
    )
    conn.commit()
    return conn


def insert_item(conn, item_id, list_id, name_key, name=None, status="active", rev=100):
    conn.execute(
        "INSERT INTO items (id, list_id, name, name_key, quantity, note, status, staple, "
        "created_by, created_at, updated_at, rev, tombstone) VALUES "
        "(?, ?, ?, ?, NULL, NULL, ?, 0, 'user-a', '2026-01-02T00:00:00+00:00', "
        "'2026-01-02T00:00:00+00:00', ?, 0)",
        (item_id, list_id, name or name_key, name_key, status, rev),
    )


def test_migrates_v1_db(tmp_path):
    path = str(tmp_path / "v1.db")
    make_v1_db(path)
    conn = db.connect(path)  # the migration runs as part of connect()
    conn.row_factory = sqlite3.Row

    # connects at v1 → the runner migrates straight through v2 to the current gate
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == (
        db.SCHEMA_VERSION
    )

    # columns added
    assert "list_id" in table_columns(conn, "items")
    assert "list_id" in table_columns(conn, "trips")
    assert "list_id" in table_columns(conn, "history")

    # data survives (backfilled columns, not dropped)
    row = conn.execute(
        "SELECT list_id FROM items WHERE id = '11111111-1111-4111-8111-111111111111'"
    ).fetchone()
    assert row["list_id"] == DEFAULT_LIST_ID
    assert conn.execute("SELECT list_id FROM trips").fetchone()["list_id"] == DEFAULT_LIST_ID
    assert conn.execute("SELECT list_id FROM history").fetchone()["list_id"] == DEFAULT_LIST_ID

    # lists table exists with the seed row
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "lists" in tables
    seed = conn.execute("SELECT * FROM lists WHERE id = ?", (DEFAULT_LIST_ID,)).fetchone()
    assert seed is not None
    assert seed["name"] == "Boodschappen"
    assert seed["tombstone"] == 0
    assert seed["rev"] >= 1  # pullable by existing devices via the watermark delta
    assert seed["rev"] > 5  # above the v1 max data rev, or migrated devices never pull it

    # unique index on (list_id, name_key) was created in v2 but DROPPED by the
    # v5 migration (constitution v1.3.0: duplicates allowed, no uniqueness)
    indexes = {
        r["name"]: r["sql"]
        for r in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'items'"
        ).fetchall()
    }
    assert "idx_items_active_name_key" not in indexes
    assert "idx_items_active_list_name_key" not in indexes

    # duplicates probe (v1.3.0): same name_key twice in ONE list is allowed
    other_list = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO lists (id, name, created_by, created_at, tombstone, rev) "
        "VALUES (?, 'Ander', 'user-a', '2026-01-02T00:00:00+00:00', 0, 99)",
        (other_list,),
    )
    conn.commit()
    insert_item(conn, "66666666-6666-4666-8666-666666666666", other_list, "melk")
    conn.commit()
    insert_item(  # same-normalized name, same list — both rows insert fine
        conn, "77777777-7777-4777-8777-777777777777", other_list, "melk", name="MELK", rev=101
    )
    conn.commit()

    # rev sequence is monotonic: migration only advanced it (seed), never reset
    rev = conn.execute("SELECT value FROM sync_meta WHERE key = 'rev'").fetchone()[0]
    assert rev >= 5  # v1 peak rev was 5

    # idempotent: re-running changes nothing
    before_lists = [tuple(r) for r in conn.execute("SELECT * FROM lists ORDER BY id").fetchall()]
    before_items = [tuple(r) for r in conn.execute("SELECT * FROM items ORDER BY id").fetchall()]
    db.migrate(conn)
    conn.commit()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    after_lists = [tuple(r) for r in conn.execute("SELECT * FROM lists ORDER BY id").fetchall()]
    assert after_lists == before_lists
    assert len(after_lists) == 2  # no second Boodschappen
    after_items = [tuple(r) for r in conn.execute("SELECT * FROM items ORDER BY id").fetchall()]
    assert after_items == before_items


def test_fresh_install_builds_v2_directly(tmp_path):
    path = str(tmp_path / "fresh.db")
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == (
        db.SCHEMA_VERSION
    )
    assert "list_id" in table_columns(conn, "items")
    assert "list_id" in table_columns(conn, "trips")
    assert "list_id" in table_columns(conn, "history")
    seed = conn.execute("SELECT * FROM lists WHERE id = ?", (DEFAULT_LIST_ID,)).fetchone()
    assert seed is not None
    assert seed["name"] == "Boodschappen"
    assert seed["tombstone"] == 0
    assert conn.execute("SELECT COUNT(*) FROM lists").fetchone()[0] == 1
    # one next_rev consumed by the seed; the sequence stays untouched otherwise
    assert conn.execute("SELECT value FROM sync_meta WHERE key = 'rev'").fetchone()[0] >= 1


def test_fresh_reopen_is_noop(tmp_path):
    path = str(tmp_path / "reopen.db")
    db.connect(path)
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == (
        db.SCHEMA_VERSION
    )
    assert conn.execute("SELECT COUNT(*) FROM lists").fetchone()[0] == 1

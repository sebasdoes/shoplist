"""Migration v5 (constitution v1.3.0: duplicates allowed per list).

Drops the partial unique index `idx_items_active_list_name_key` on
(list_id, name_key). No data changes, the name_key column stays, the v4
backfill and sync_meta lift are untouched — and the drop is idempotent.
Same-normalized names now insert freely on the SAME list.
"""

import sqlite3

from app import db

DEFAULT_LIST_ID = "00000000-0000-4000-8000-000000000001"

INDEX_NAME = "idx_items_active_list_name_key"


def item_indexes(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        r["name"]: r["sql"]
        for r in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'items'"
        ).fetchall()
    }


def insert_dupe_pair(conn: sqlite3.Connection) -> None:
    """Same-normalized name twice on ONE list (the v2-era unique index's job)."""
    for item_id, name in (
        ("66666666-6666-4666-8666-666666666666", "melk"),
        ("77777777-7777-4777-8777-777777777777", "MELK"),
    ):
        conn.execute(
            "INSERT INTO items (id, list_id, name, name_key, quantity, note, status, staple, "
            "created_by, created_at, updated_at, rev, tombstone, sort_order) VALUES "
            "(?, ?, ?, ?, NULL, NULL, 'active', 0, 'user-a', "
            "'2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00', ?, 0, 0)",
            (item_id, DEFAULT_LIST_ID, name, "melk", 100),
        )


def test_fresh_install_has_no_dedupe_index(tmp_path):
    path = str(tmp_path / "fresh.db")
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert "name_key" in {
        r["name"] for r in conn.execute("PRAGMA table_info(items)").fetchall()
    }  # the column stays (harmless)
    assert INDEX_NAME not in item_indexes(conn)
    connect2 = db.connect(path)  # reopen: fresh DDL re-runs nothing, drop idempotent
    assert INDEX_NAME not in item_indexes(connect2)


def test_v1_database_migrates_to_v5_without_index(tmp_path):
    """A v1 db migrates through v2 (which creates the index) → v5 (which drops it)."""
    path = str(tmp_path / "v1.db")
    conn = sqlite3.connect(path)
    conn.executescript(db._SCHEMA_V1)
    conn.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (1, '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute(
        "SELECT MAX(version) FROM schema_migrations"
    ).fetchone()[0] == db.SCHEMA_VERSION
    assert INDEX_NAME not in item_indexes(conn)
    # and the data survives: backfill + drop, no data changes
    assert conn.execute("SELECT COUNT(*) FROM lists").fetchone()[0] == 1


def test_v4_database_migrates_to_v5(tmp_path):
    """A database already at v4 gets ONLY the index drop (no second v4 backfill)."""
    path = str(tmp_path / "v4.db")
    conn = db.connect(path)
    # build a literal v4 database: fresh install, then force user_version back
    # so the v5 step re-runs (simulating a v4-era deployment)
    conn.execute("PRAGMA user_version = 4")
    conn.execute("DELETE FROM schema_migrations WHERE version = 5")
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM lists").fetchone()[0]
    revs_before = conn.execute("SELECT SUM(rev) FROM items").fetchone()[0]

    db.migrate(conn)  # re-run: current = 4 → only the v5 drop applies
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert INDEX_NAME not in item_indexes(conn)
    assert conn.execute("SELECT COUNT(*) FROM lists").fetchone()[0] == count
    # the v4 backfill did NOT re-run: revs unchanged
    assert conn.execute("SELECT SUM(rev) FROM items").fetchone()[0] == revs_before


def test_duplicates_insert_after_migration(tmp_path):
    path = str(tmp_path / "dupes.db")
    conn = db.connect(path)
    insert_dupe_pair(conn)
    conn.commit()
    rows = conn.execute(
        "SELECT id, name FROM items WHERE list_id = ? AND tombstone = 0 ORDER BY id",
        (DEFAULT_LIST_ID,),
    ).fetchall()
    assert [r["name"] for r in rows] == ["melk", "MELK"]  # both live, display forms kept
    db.migrate(conn)
    assert INDEX_NAME not in item_indexes(conn)  # idempotent

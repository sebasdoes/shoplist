"""T001 (written FIRST, TDD) — migration v2→v3 per feature 003 (menu polish).

Adds `lists.sort_order` (INTEGER NOT NULL DEFAULT 0), backfills a sequential
order by (created_at, rowid), sets `PRAGMA user_version = 3`, and is
idempotent. Fresh installs gain `sort_order` directly in the schema.
"""

import sqlite3

from app import db

DEFAULT_LIST_ID = "00000000-0000-4000-8000-000000000001"
LIST_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LIST_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
LIST_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def make_v2_db(path) -> sqlite3.Connection:
    """Build a literal schema-v2 database (as feature 002 shipped it): lists
    WITHOUT sort_order plus the seeded default list and three more to
    exercise the backfill."""
    conn = sqlite3.connect(path)
    conn.executescript(db._SCHEMA_V2)
    conn.execute(
        "CREATE TABLE schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO schema_migrations(version, applied_at) "
        "VALUES (2, '2026-01-01T00:00:00+00:00')"
    )
    conn.execute("PRAGMA user_version = 2")
    rows = [
        (DEFAULT_LIST_ID, "Boodschappen", "2025-12-31T00:00:00+00:00", 9),
        (LIST_A, "A", "2026-01-02T00:00:00+00:00", 10),
        (LIST_B, "B", "2026-01-01T00:00:00+00:00", 11),
        (LIST_C, "C", "2026-01-02T00:00:00+00:00", 12),  # created_at tie with A, later rowid
    ]
    for list_id, name, created_at, rev in rows:
        conn.execute(
            "INSERT INTO lists (id, name, created_by, created_at, tombstone, rev) "
            "VALUES (?, ?, 'user-a', ?, 0, ?)",
            (list_id, name, created_at, rev),
        )
    conn.commit()
    return conn


def list_columns(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in conn.execute("PRAGMA table_info(lists)").fetchall()}


def sort_orders(conn: sqlite3.Connection) -> dict[str, int]:
    return {r["id"]: r["sort_order"] for r in conn.execute("SELECT id, sort_order FROM lists")}


def test_migrates_v2_db_adds_sort_order(tmp_path):
    path = str(tmp_path / "v2.db")
    make_v2_db(path)
    conn = db.connect(path)  # migration runs as part of connect()

    assert "sort_order" in list_columns(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    migrated = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert migrated == db.SCHEMA_VERSION

    # sequential backfill by (created_at, rowid): default list oldest → 0, then
    # B; the created_at tie between A and C breaks by rowid
    assert sort_orders(conn) == {DEFAULT_LIST_ID: 0, LIST_B: 1, LIST_A: 2, LIST_C: 3}

    # pre-existing columns/data survive untouched
    row = conn.execute("SELECT name, rev, created_at FROM lists WHERE id = ?", (LIST_A,)).fetchone()
    assert row["name"] == "A"
    assert row["rev"] >= 10  # v3 bumps list revs so delta pulls propagate
    assert row["created_at"] == "2026-01-02T00:00:00+00:00"


def test_migration3_is_idempotent(tmp_path):
    path = str(tmp_path / "v2.db")
    make_v2_db(path)
    conn = db.connect(path)
    conn.row_factory = sqlite3.Row
    before = [tuple(r) for r in conn.execute("SELECT * FROM lists ORDER BY id").fetchall()]

    db.migrate(conn)
    conn.commit()

    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    after = [tuple(r) for r in conn.execute("SELECT * FROM lists ORDER BY id").fetchall()]
    assert after == before
    # ALTER did not run twice (duplicate column would have raised)
    assert sort_orders(conn) == {DEFAULT_LIST_ID: 0, LIST_B: 1, LIST_A: 2, LIST_C: 3}


def test_fresh_install_gains_sort_order_directly(tmp_path):
    path = str(tmp_path / "fresh.db")
    conn = db.connect(path)

    assert "sort_order" in list_columns(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    migrated = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert migrated == db.SCHEMA_VERSION
    # the seeded default list is the only row → sequential order starts at 0
    assert sort_orders(conn) == {DEFAULT_LIST_ID: 0}
    seed = conn.execute(
        "SELECT name, tombstone FROM lists WHERE id = ?", (DEFAULT_LIST_ID,)
    ).fetchone()
    assert seed["name"] == "Boodschappen"
    assert seed["tombstone"] == 0


def test_v1_db_migrates_straight_to_v4(tmp_path):
    """A v1 database migrates through v2/v3 to v4 in one connect()."""
    from tests.test_migration2 import make_v1_db

    path = str(tmp_path / "v1.db")
    make_v1_db(path)
    conn = db.connect(path)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert "sort_order" in list_columns(conn)
    # seed row is inserted after the backfill → keeps its default 0
    assert sort_orders(conn) == {DEFAULT_LIST_ID: 0}
    # v4 items backfill ran: the one active, live item orders the queue
    item_orders = {
        r["id"]: r["sort_order"] for r in conn.execute("SELECT id, sort_order FROM items")
    }
    assert item_orders["11111111-1111-4111-8111-111111111111"] == 0  # active → 0
    assert item_orders["22222222-2222-4222-8222-222222222222"] == 0  # completed → untouched
    assert item_orders["33333333-3333-4333-8333-333333333333"] == 0  # tombstoned → untouched
    seed = conn.execute("SELECT name FROM lists WHERE id = ?", (DEFAULT_LIST_ID,)).fetchone()
    assert seed["name"] == "Boodschappen"
    # v2 backfills still applied
    row = conn.execute(
        "SELECT list_id FROM items WHERE id = '11111111-1111-4111-8111-111111111111'"
    ).fetchone()
    assert row["list_id"] == DEFAULT_LIST_ID

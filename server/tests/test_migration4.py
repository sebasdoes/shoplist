"""T001 (written FIRST, TDD) — migration v3→v4 per feature 004 (item reorder).

Adds `items.sort_order` (INTEGER NOT NULL DEFAULT 0), backfills a
sequential order over the ACTIVE, live items of each list by
(created_at DESC, rev DESC) — newest first, matching the current
display — LIFTS sync_meta above the max data rev BEFORE bumping the
backfilled rows (v2/v3 lesson), sets `PRAGMA user_version = 4`, and is
idempotent. Fresh installs gain the column directly in the schema.
"""

import sqlite3

from app import db

DEFAULT_LIST_ID = "00000000-0000-4000-8000-000000000001"
LIST_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

NEWEST = "11111111-1111-4111-8111-111111111111"   # created_at 2026-01-03, rev 12
MIDDLE = "22222222-2222-4222-8222-222222222222"   # created_at 2026-01-02, rev 11
OLDEST = "33333333-3333-4333-8333-333333333333"   # created_at 2026-01-01, rev 10
DONE = "44444444-4444-4444-8444-444444444444"     # completed — not part of the queue
TOMB = "55555555-5555-4555-8555-555555555555"     # active but tombstoned — skipped
TIE_HIGH = "66666666-6666-4666-8666-666666666666"  # LIST_A: same created_at as…
TIE_LOW = "77777777-7777-4777-8777-777777777777"   # …rev DESC breaks the tie


def make_v3_db(path) -> sqlite3.Connection:
    """Build a literal schema-v3 database (as feature 003 shipped it): items
    WITHOUT sort_order plus the seeded lists and items with known
    created_at/rev values."""
    conn = sqlite3.connect(path)
    conn.executescript(db._SCHEMA_V3)
    conn.execute(
        "CREATE TABLE schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (3, '2026-01-01T00:00:00+00:00')"
    )
    conn.execute("PRAGMA user_version = 3")
    for list_id, name, created_at, rev in [
        (DEFAULT_LIST_ID, "Boodschappen", "2025-12-31T00:00:00+00:00", 9),
        (LIST_A, "A", "2026-01-02T00:00:00+00:00", 13),
    ]:
        conn.execute(
            "INSERT INTO lists (id, name, created_by, created_at, tombstone, rev) "
            "VALUES (?, ?, 'user-a', ?, 0, ?)",
            (list_id, name, created_at, rev),
        )
    rows = [
        # default list: three active items, newest first is the current display
        (NEWEST, DEFAULT_LIST_ID, "melk", "2026-01-03T00:00:00+00:00", 12, "active", 0),
        (MIDDLE, DEFAULT_LIST_ID, "brood", "2026-01-02T00:00:00+00:00", 11, "active", 0),
        (OLDEST, DEFAULT_LIST_ID, "rijst", "2026-01-01T00:00:00+00:00", 10, "active", 0),
        (DONE, DEFAULT_LIST_ID, "koffie", "2026-01-01T00:00:00+00:00", 8, "completed", 0),
        (TOMB, DEFAULT_LIST_ID, "spruitjes", "2026-01-01T00:00:00+00:00", 7, "active", 1),
        # list A: created_at tie — the higher rev is the newer row
        (TIE_HIGH, LIST_A, "thee", "2026-01-05T00:00:00+00:00", 15, "active", 0),
        (TIE_LOW, LIST_A, "suiker", "2026-01-05T00:00:00+00:00", 14, "active", 0),
    ]
    for item_id, list_id, name, created_at, rev, status, tombstone in rows:
        conn.execute(
            "INSERT INTO items (id, list_id, name, name_key, quantity, note, status, staple, "
            "created_by, created_at, updated_at, rev, tombstone) VALUES "
            "(?, ?, ?, ?, NULL, NULL, ?, 0, 'user-a', ?, ?, ?, ?)",
            (item_id, list_id, name, name.lower(), status, created_at, created_at, rev, tombstone),
        )
    # sync_meta sits BELOW the max data rev, like a database the lift lesson
    # was learned from: devices may already be past these revs.
    conn.execute("UPDATE sync_meta SET value = 3 WHERE key = 'rev'")
    conn.commit()
    return conn


def item_columns(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in conn.execute("PRAGMA table_info(items)").fetchall()}


def sort_orders(conn: sqlite3.Connection) -> dict[str, int]:
    return {r["id"]: r["sort_order"] for r in conn.execute("SELECT id, sort_order FROM items")}


def test_migrates_v3_db_adds_item_sort_order(tmp_path):
    path = str(tmp_path / "v3.db")
    make_v3_db(path)
    conn = db.connect(path)  # migration runs as part of connect()
    conn.row_factory = sqlite3.Row

    assert "sort_order" in item_columns(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 4

    # sequential backfill over the ACTIVE, live items of each list, newest
    # first (created_at DESC, rev DESC — matches the current display order)
    assert sort_orders(conn)[NEWEST] == 0
    assert sort_orders(conn)[MIDDLE] == 1
    assert sort_orders(conn)[OLDEST] == 2
    # list A's created_at tie breaks by rev DESC (higher rev = newer)
    assert sort_orders(conn)[TIE_HIGH] == 0
    assert sort_orders(conn)[TIE_LOW] == 1

    # ticked and tombstoned rows are not part of the queue — untouched
    assert sort_orders(conn)[DONE] == 0
    assert sort_orders(conn)[TOMB] == 0

    # pre-existing columns/data survive untouched
    row = conn.execute("SELECT name, list_id FROM items WHERE id = ?", (MIDDLE,)).fetchone()
    assert row["name"] == "brood"
    assert row["list_id"] == DEFAULT_LIST_ID


def test_migration4_lifts_sync_meta_and_bumps_backfilled_revs(tmp_path):
    path = str(tmp_path / "v3b.db")
    make_v3_db(path)
    pre_max = 15  # the highest data rev in the fixture (TIE_HIGH)
    pre_meta = 3
    conn = db.connect(path)
    conn.row_factory = sqlite3.Row

    # sync_meta lifted above the max data rev BEFORE the row bumps: devices
    # already past these revs must still receive the backfilled order
    meta = conn.execute("SELECT value FROM sync_meta WHERE key = 'rev'").fetchone()[0]
    assert meta >= pre_max and meta > pre_meta

    # every backfilled active row got a fresh rev above ALL previous revs
    for item_id in (NEWEST, MIDDLE, OLDEST, TIE_HIGH, TIE_LOW):
        rev = conn.execute("SELECT rev FROM items WHERE id = ?", (item_id,)).fetchone()[0]
        assert rev > pre_max, item_id
    # non-queued rows keep their rev
    assert conn.execute("SELECT rev FROM items WHERE id = ?", (DONE,)).fetchone()[0] == 8
    assert conn.execute("SELECT rev FROM items WHERE id = ?", (TOMB,)).fetchone()[0] == 7
    # the sequence only moved forward (it stays >= every data rev; the seed's
    # INSERT OR IGNORE still consumes one slot)
    assert meta > conn.execute("SELECT MAX(rev) FROM items").fetchone()[0]


def test_migration4_is_idempotent(tmp_path):
    path = str(tmp_path / "again.db")
    make_v3_db(path)
    conn = db.connect(path)
    before = [tuple(r) for r in conn.execute("SELECT * FROM items ORDER BY id").fetchall()]
    before_meta = conn.execute("SELECT value FROM sync_meta WHERE key = 'rev'").fetchone()[0]

    db.migrate(conn)
    conn.commit()

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    after = [tuple(r) for r in conn.execute("SELECT * FROM items ORDER BY id").fetchall()]
    assert after == before  # duplicate ALTER would have raised; revs not re-bumped
    meta_after = conn.execute("SELECT value FROM sync_meta WHERE key = 'rev'").fetchone()[0]
    assert meta_after == before_meta
    assert sort_orders(conn)[NEWEST] == 0  # order intact


def test_fresh_install_gains_item_sort_order_directly(tmp_path):
    path = str(tmp_path / "fresh.db")
    conn = db.connect(path)
    conn.row_factory = sqlite3.Row

    assert "sort_order" in item_columns(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 4
    # the seeded default list exists; lists still carry their v3 sort_order
    seed = conn.execute("SELECT name FROM lists WHERE id = ?", (DEFAULT_LIST_ID,)).fetchone()
    assert seed["name"] == "Boodschappen"
    assert conn.execute("SELECT sort_order FROM lists WHERE id = ?", (DEFAULT_LIST_ID,)).fetchone()[
        "sort_order"
    ] == 0


def test_v2_db_migrates_straight_to_v4(tmp_path):
    """A v2 database migrates through v3 to v4 in one connect()."""
    from tests.test_migration3 import make_v2_db

    path = str(tmp_path / "v2.db")
    make_v2_db(path)
    conn = db.connect(path)
    conn.row_factory = sqlite3.Row

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    assert "sort_order" in item_columns(conn)
    # make_v2_db inserts no items, so the items backfill is a no-op;
    # the v3 list backfill still ran (Boodschappen + A + B + C sequential)
    list_orders = {
        r["id"]: r["sort_order"] for r in conn.execute("SELECT id, sort_order FROM lists")
    }
    assert sorted(list_orders.values()) == [0, 1, 2, 3]

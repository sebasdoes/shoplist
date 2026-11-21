"""Migration cold-start race (TDD): migrate() must read the schema version and
run every ALTER inside ONE BEGIN IMMEDIATE transaction, so two connections
opening a pre-v5 database concurrently cannot both attempt the column ALTERs
(the loser used to 500 with `duplicate column name`)."""

import sqlite3
import threading
import time

from tests.test_migration2 import make_v1_db


def test_version_read_waits_for_write_lock(tmp_path, monkeypatch):
    """The discriminating property of the fix: the schema-version read may only
    happen while the BEGIN IMMEDIATE write transaction is held. If another
    connection completes a migration while we wait for the write lock, our
    (later) read sees the new version and no ALTERs re-run."""
    import app.db as db

    path = str(tmp_path / "race.db")
    make_v1_db(path)

    holder = sqlite3.connect(path)
    holder.execute("PRAGMA busy_timeout=5000")
    # Convert to WAL first (as db.connect would on a cold start) so the opener
    # is not busy-failed by the journal-mode switch instead of the migration.
    holder.execute("PRAGMA journal_mode=WAL")

    # Spy on the version read to observe WHEN it happens.
    read_times: list[float] = []
    original = db._current_schema_version

    def spy(conn: sqlite3.Connection) -> int:
        read_times.append(time.monotonic())
        return original(conn)

    monkeypatch.setattr(db, "_current_schema_version", spy)

    holder.execute("BEGIN IMMEDIATE")  # a competing cold-start holds the write lock
    release: dict[str, float] = {}

    def opener() -> None:
        conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        db.migrate(conn)
        release["user_version"] = conn.execute("PRAGMA user_version").fetchone()[0]
        release["list_id_cols"] = conn.execute(
            "SELECT COUNT(*) FROM pragma_table_info('items') WHERE name = 'list_id'"
        ).fetchone()[0]
        release["lists"] = conn.execute(
            "SELECT COUNT(*) FROM lists WHERE id = ?",
            ("00000000-0000-4000-8000-000000000001",),
        ).fetchone()[0]
        conn.close()

    thread = threading.Thread(target=opener)
    thread.start()
    time.sleep(0.3)  # the opener must be blocked in migrate() during the hold
    holder.execute("COMMIT")
    released_at = time.monotonic()
    holder.close()
    thread.join(timeout=15)

    assert release.get("user_version") == db.SCHEMA_VERSION
    assert release["list_id_cols"] == 1  # ALTER ran exactly once
    assert release["lists"] == 1  # seeded exactly once
    assert len(read_times) == 1
    # The version read happened AFTER the competing write transaction was
    # released — i.e. inside BEGIN IMMEDIATE, not on stale pre-lock state.
    # (Pre-fix code read the version while the lock was still held.)
    assert read_times[0] > released_at, (
        "schema version was read before acquiring the migration write lock"
    )


def test_two_concurrent_cold_starts_both_succeed(tmp_path):
    """End-to-end race smoke: two parallel cold-start db.connect() calls on the
    same v1 database both finish cleanly with one schema — no duplicate-column
    500s, exactly one seed."""
    import app.db as db

    path = str(tmp_path / "sprint.db")
    make_v1_db(path)

    barrier = threading.Barrier(2)
    errors: list[Exception | None] = []

    def opener() -> None:
        try:
            barrier.wait(timeout=10)
            conn = db.connect(path)
            errors.append(None)
            conn.close()
        except Exception as exc:  # duplicate column / unexpected lock failure
            errors.append(exc)

    threads = [threading.Thread(target=opener) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == [None, None], errors
    check = sqlite3.connect(path)
    assert check.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert (
        check.execute(
            "SELECT COUNT(*) FROM pragma_table_info('items') WHERE name = 'list_id'"
        ).fetchone()[0]
        == 1
    )
    check.close()

"""T024 — idempotent trip transaction (history snapshots, keep/drop, staple re-add).

Per data-model transitions + research D5: one trip id, ONE transaction, repeated id is a no-op.
"""

from __future__ import annotations

import sqlite3

from . import db
from .auth import AuthUser
from .models import Readded, TripDecision, TripMutation, TripResponse
from .sync import SyncError


def _fetch_item(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def _bump(conn: sqlite3.Connection, item_id: str, status: str) -> None:
    conn.execute(
        "UPDATE items SET status = ?, updated_at = ?, rev = ? WHERE id = ?",
        (status, db.stamp(), db.next_rev(conn), item_id),
    )


def _history_snapshot(
    conn: sqlite3.Connection, row: sqlite3.Row, outcome: str, actor: str
) -> sqlite3.Row:
    """bought→history snapshot rows / drop→not_bought (data-model history)."""
    row_id = db.new_id()
    conn.execute(
        "INSERT INTO history (id, item_id, name, quantity, note, outcome, staple_snapshot, actor, "
        "occurred_at, rev) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row_id,
            row["id"],
            row["name"],
            row["quantity"],
            row["note"],
            outcome,
            int(bool(row["staple"])),
            actor,
            db.stamp(),
            db.next_rev(conn),
        ),
    )
    return _fetch_row(conn, "history", row_id)


def _fetch_row(conn: sqlite3.Connection, table: str, row_id: str) -> sqlite3.Row:
    return conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()


def _apply_decision(
    conn: sqlite3.Connection, decision: TripDecision, row: sqlite3.Row, actor: str
) -> tuple[str, bool]:
    """Returns (outcome, applied). Skips items not in an applicable state."""
    outcome = decision.outcome
    if outcome == "bought":
        if row["status"] != "active" or row["tombstone"]:
            return outcome, False
        _bump(conn, row["id"], "completed")  # completed never returns to active
        _history_snapshot(conn, row, "bought", actor)
        return outcome, True
    if outcome == "not_bought":
        if row["status"] != "active" or row["tombstone"]:
            return outcome, False
        _bump(conn, row["id"], "dropped")
        _history_snapshot(conn, row, "not_bought", actor)
        return outcome, True
    # kept → still active, no history row
    return outcome, False


def apply_trip(conn: sqlite3.Connection, trip: TripMutation, user: AuthUser) -> TripResponse:
    existing = conn.execute("SELECT id FROM trips WHERE id = ?", (trip.id,)).fetchone()
    if existing is not None:
        # trip id = idempotency key: {"duplicate": true}, no effects
        return TripResponse(id=trip.id, duplicate=True, readded=[])

    # validate explicit item references before writing anything (transaction atomicity)
    explicit: list[tuple[TripDecision, sqlite3.Row]] = []
    bulk: list[TripDecision] = []
    for decision in trip.decisions:
        if decision.item_id is not None:
            row = _fetch_item(conn, decision.item_id)
            if row is None or row["tombstone"]:
                raise SyncError(f"unknown item in trip decisions: {decision.item_id}", 400)
            explicit.append((decision, row))
        else:
            if decision.outcome == "bought":
                raise SyncError("bought decision requires item_id", 422)
            bulk.append(decision)

    actor = user.subject
    now = db.stamp()
    conn.execute(
        "INSERT INTO trips (id, completed_by, completed_at, rev) VALUES (?, ?, ?, ?)",
        (trip.id, actor, now, db.next_rev(conn)),
    )

    decided_ids: set[str] = set()
    bought_staples: list[sqlite3.Row] = []
    for decision, row in explicit:
        outcome, applied = _apply_decision(conn, decision, row, actor)
        if applied:
            decided_ids.add(row["id"])
            if outcome == "bought" and row["staple"]:
                bought_staples.append(row)

    # bulk decisions (no item_id) cover the unticked remainder
    if bulk:
        remaining = conn.execute(
            "SELECT * FROM items WHERE status = 'active' AND tombstone = 0"
        ).fetchall()
        for decision in bulk:
            for row in remaining:
                if row["id"] in decided_ids:
                    continue
                _apply_decision(conn, decision, row, actor)

    readded: list[Readded] = []
    for row in bought_staples:
        if not row["name_key"]:
            continue
        # staple already-active → no duplicate (partial unique: active + tombstone=0)
        active = conn.execute(
            "SELECT id FROM items WHERE name_key = ? AND status = 'active' AND tombstone = 0",
            (row["name_key"],),
        ).fetchone()
        if active is not None:
            continue
        new_id = db.new_id()
        conn.execute(
            "INSERT INTO items (id, name, name_key, quantity, note, status, staple, created_by, "
            "created_at, updated_at, rev, tombstone) "
            "VALUES (?, ?, ?, ?, ?, 'active', 1, ?, ?, ?, ?, 0)",
            (
                new_id,
                row["name"],
                row["name_key"],
                row["quantity"],  # prefilled from history.staple_snapshot/quantity/note
                row["note"],
                actor,
                now,
                now,
                db.next_rev(conn),
            ),
        )
        readded.append(Readded(id=new_id, name=row["name"]))

    return TripResponse(id=trip.id, duplicate=False, readded=readded)

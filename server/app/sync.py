"""T018 — push (per-field LWW, dedupe merge, tombstones) and pull (watermark, snapshot flag)."""

from __future__ import annotations

import sqlite3
import unicodedata

from . import db
from .auth import AuthUser
from .models import (
    Applied,
    PullRequest,
    PullResponse,
    PushRequest,
    PushResponse,
    UpsertItem,
    _row_to_history,
    _row_to_item,
)

# data-model rev: delta cap for the full-snapshot flag
PULL_SNAPSHOT_THRESHOLD = 500

_FIELD_COLUMNS = {
    "name": "name",
    "quantity": "quantity",
    "note": "note",
    "staple": "staple",
    "tombstone": "tombstone",
}


class SyncError(Exception):
    def __init__(self, detail: str, status_code: int = 422):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def name_key(name: str) -> str:
    """research D6: casefold + trim + collapse internal whitespace + strip diacritics (NFKD)."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


def _fetch_row(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def _find_active_by_name_key(
    conn: sqlite3.Connection, key: str, exclude_id: str | None = None
) -> sqlite3.Row | None:
    if not key:
        return None
    row = conn.execute(
        "SELECT * FROM items WHERE name_key = ? AND status = 'active'"
        " AND tombstone = 0 AND id != ?",
        (key, exclude_id or ""),
    ).fetchone()
    return row


def _fetch_applied(row: sqlite3.Row, merged_into: str | None) -> Applied:
    return Applied(
        id=row["id"], rev=row["rev"], updated_at=row["updated_at"], merged_into=merged_into
    )


def upsert_apply(conn: sqlite3.Connection, item: UpsertItem, user: AuthUser) -> Applied:
    """Apply one pushed item mutation (in-order, inside the push transaction)."""
    fields = item.provided_fields()
    existing = _fetch_row(conn, item.id)

    if existing is not None:
        if existing["tombstone"]:
            # Tombstone precedence: a deleted row never resurrects; concurrent edits lose.
            return _fetch_applied(existing, None)

        is_delete = "tombstone" in fields and item.tombstone is True
        new_key = name_key(item.name) if "name" in fields else None
        survivor = (
            None
            if is_delete or new_key is None
            else _find_active_by_name_key(conn, new_key, item.id)
        )

        now = db.stamp()

        # Dedupe hit: fold the pushed fields onto the existing active row.
        if survivor is not None:
            _apply_fields(
                conn,
                item_id=survivor["id"],
                fields=fields,
                item=item,
                user=user,
                skip_name=True,  # the survivor keeps its display form; identity is the name_key
            )
            # Retire the pushed row so the fold propagates to other devices.
            conn.execute(
                "UPDATE items SET tombstone = 1, updated_at = ?, rev = ? WHERE id = ?",
                (now, db.next_rev(conn), item.id),
            )
            survivor_row = _fetch_row(conn, survivor["id"])
            return _fetch_applied(survivor_row, survivor["id"])

        _apply_fields(conn, item_id=item.id, fields=fields, item=item, user=user, skip_name=False)
        return _fetch_applied(_fetch_row(conn, item.id), None)

    # New row from the server's perspective.
    is_delete = "tombstone" in fields and item.tombstone is True
    if "name" in fields:
        key = name_key(item.name)
        survivor = _find_active_by_name_key(conn, key)
        if survivor is not None:
            _apply_fields(
                conn,
                item_id=survivor["id"],
                fields=fields,
                item=item,
                user=user,
                skip_name=True,
            )
            survivor_row = _fetch_row(conn, survivor["id"])
            return _fetch_applied(survivor_row, survivor["id"])
    elif not is_delete:
        raise SyncError("name required after trim, 1–200 chars", 422)

    rev = db.next_rev(conn)
    now = db.stamp()
    name = (item.name or "").strip() if "name" in fields else ""
    key = name_key(name) if name else ""
    conn.execute(
        "INSERT INTO items (id, name, name_key, quantity, note, status, staple, created_by, "
        "created_at, updated_at, rev, tombstone) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)",
        (
            item.id,
            name,
            key,
            item.quantity,
            item.note,
            1 if item.staple else 0,
            user.subject,
            now,
            now,
            rev,
            1 if is_delete else 0,
        ),
    )
    return _fetch_applied(_fetch_row(conn, item.id), None)


def _apply_fields(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    fields: set[str],
    item: UpsertItem,
    user: AuthUser,
    skip_name: bool,
) -> None:
    """Per-field LWW: only fields present in the pushed field-set are written."""
    assignments: list[str] = []
    params: list[object] = []
    for field, column in _FIELD_COLUMNS.items():
        if field not in fields:
            continue
        if field == "name":
            if skip_name:
                continue
            assignments.append("name_key = ?")
            params.append(name_key(item.name or ""))
        value: object
        if field == "staple":
            value = 1 if item.staple else 0
        elif field == "tombstone":
            value = 1 if item.tombstone else 0
        else:
            value = getattr(item, field)
        assignments.append(f"{column} = ?")
        params.append(value)
    assignments.append("updated_at = ?")
    params.append(db.stamp())
    assignments.append("rev = ?")
    params.append(db.next_rev(conn))
    params.append(item_id)
    conn.execute(f"UPDATE items SET {', '.join(assignments)} WHERE id = ?", params)


def push(conn: sqlite3.Connection, request: PushRequest, user: AuthUser) -> PushResponse:
    applied: list[Applied] = []
    trip_response = None
    conn.execute("BEGIN IMMEDIATE")
    try:
        db.upsert_user(conn, user.subject, user.name)
        for mutation in request.mutations:
            if mutation.op == "upsert_item":
                assert mutation.item is not None  # enforced by the model validator
                applied.append(upsert_apply(conn, mutation.item, user))
            else:
                assert mutation.trip is not None
                from . import trips as trips_mod

                trip_response = trips_mod.apply_trip(conn, mutation.trip, user)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return PushResponse(applied=applied, trip=trip_response)


def pull(conn: sqlite3.Connection, request: PullRequest) -> PullResponse:
    watermark = request.watermark
    current = db.current_rev(conn)
    delta = conn.execute("SELECT COUNT(*) FROM items WHERE rev > ?", (watermark,)).fetchone()[0]

    if delta > PULL_SNAPSHOT_THRESHOLD:
        # contracts/api.md: delta > 500 rows → full snapshot flag, fresh state
        rows = conn.execute("SELECT * FROM items ORDER BY rev").fetchall()
        full_snapshot = True
    else:
        rows = conn.execute(
            "SELECT * FROM items WHERE rev > ? ORDER BY rev", (watermark,)
        ).fetchall()
        full_snapshot = False

    history = []
    if request.include_history:
        history = conn.execute(
            "SELECT * FROM history ORDER BY occurred_at DESC LIMIT 1000"
        ).fetchall()

    return PullResponse(
        watermark=current,
        items=[_row_to_item(r) for r in rows],
        history=[_row_to_history(r) for r in history],
        full_snapshot=full_snapshot,
    )

"""T018 — push (per-field LWW, tombstones) and pull (watermark, snapshot flag).

Constitution v1.3.0: auto-dedupe of identical names is REMOVED — pushes never
fold onto a survivor, every upsert echoes exactly ONE Applied row.
"""

from __future__ import annotations

import sqlite3
import unicodedata
import uuid
from collections.abc import Sequence

from . import db
from .auth import AuthUser
from .models import (
    Applied,
    AppliedGroup,
    AppliedList,
    ListPayload,
    PullRequest,
    PullResponse,
    PushRequest,
    PushResponse,
    RejectedMutation,
    UpsertItem,
    _row_to_history,
    _row_to_item,
    _row_to_list,
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


def require_live_list(conn: sqlite3.Connection, list_id: str) -> sqlite3.Row:
    """v2: items/trips must reference a live (non-tombstoned) list —
    unknown or tombstoned list_id → 400 + whole-batch rollback."""
    row = conn.execute("SELECT * FROM lists WHERE id = ?", (list_id,)).fetchone()
    if row is None:
        raise SyncError(f"unknown list: {list_id}", 400)
    if row["tombstone"]:
        raise SyncError(f"list is deleted: {list_id}", 400)
    return row


def _fetch_row(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def _fetch_applied(row: sqlite3.Row) -> Applied:
    """Echo the row itself. v1.3.0: merged_into is ALWAYS None (wire compat)."""
    return Applied(
        id=row["id"],
        rev=row["rev"],
        updated_at=row["updated_at"],
        merged_into=None,
    )


def top_insert_sort_order(conn: sqlite3.Connection, list_id: str) -> int:
    """v4: a NEW active item enters at the TOP of its list's queue —
    one below (before) the current minimum active sort_order. Lists without
    active items start at -1 → 0, 1, ... as items keep arriving above."""
    row = conn.execute(
        "SELECT MIN(sort_order) FROM items WHERE list_id = ? AND status = 'active'"
        " AND tombstone = 0",
        (list_id,),
    ).fetchone()
    minimum = row[0]
    return (minimum if minimum is not None else 0) - 1


def upsert_apply(conn: sqlite3.Connection, item: UpsertItem, user: AuthUser) -> list[Applied]:
    """Apply one pushed item mutation (in-order, inside the push transaction).

    Constitution v1.3.0: NO dedupe, NO merge, NO survivor — a pushed id is
    its own row (same-normalized names coexist on one list). Tombstone
    precedence is kept: a tombstoned row never resurrects. Exactly ONE
    Applied is returned: the row itself (merged_into stays None)."""
    require_live_list(conn, item.list_id)  # unknown/tombstoned → 400, batch rolls back
    fields = item.provided_fields()
    existing = _fetch_row(conn, item.id)

    if existing is not None:
        if existing["tombstone"]:
            # Tombstone precedence: a deleted row never resurrects; concurrent edits lose.
            return [_fetch_applied(existing)]
        # Plain per-field LWW apply (name included — no fold, no skip).
        _apply_fields(conn, item_id=item.id, fields=fields, item=item, user=user)
        return [_fetch_applied(_fetch_row(conn, item.id))]

    # New row from the server's perspective — always a plain insert.
    is_delete = "tombstone" in fields and item.tombstone is True
    if "name" not in fields and not is_delete:
        raise SyncError("name required after trim, 1–200 chars", 422)

    rev = db.next_rev(conn)
    now = db.stamp()
    name = (item.name or "").strip() if "name" in fields else ""
    key = name_key(name) if name else ""
    # v4: new rows enter at the TOP of the queue; tombstone rows carry an
    # unused sort_order (they never display).
    sort_order = 0 if is_delete else top_insert_sort_order(conn, item.list_id)
    conn.execute(
        "INSERT INTO items (id, list_id, name, name_key, quantity, note, status, staple, "
        "created_by, created_at, updated_at, rev, tombstone, sort_order) "
        "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)",
        (
            item.id,
            item.list_id,
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
            sort_order,
        ),
    )
    return [_fetch_applied(_fetch_row(conn, item.id))]


def _apply_fields(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    fields: set[str],
    item: UpsertItem,
    user: AuthUser,
) -> None:
    """Per-field LWW: only fields present in the pushed field-set are written."""
    assignments: list[str] = []
    params: list[object] = []
    for field, column in _FIELD_COLUMNS.items():
        if field not in fields:
            continue
        if field == "name":
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


def apply_create_list(
    conn: sqlite3.Connection, payload: ListPayload, user: AuthUser
) -> AppliedList:
    """create_list: validated/trimmed name (pydantic), NOT unique; duplicate id is
    an idempotent no-op returning the row's current state (outbox retry safety).
    v3 lands the new row at the back of the household order (max sort_order + 1,
    tombstoned rows included so freed slots are never reused)."""
    existing = conn.execute("SELECT * FROM lists WHERE id = ?", (payload.id,)).fetchone()
    if existing is not None:
        return AppliedList(
            id=existing["id"],
            name=existing["name"],
            rev=existing["rev"],
            tombstone=bool(existing["tombstone"]),
            sort_order=existing["sort_order"],
        )
    max_row = conn.execute("SELECT MAX(sort_order) FROM lists").fetchone()
    sort_order = (max_row[0] if max_row[0] is not None else -1) + 1
    rev = db.next_rev(conn)
    conn.execute(
        "INSERT INTO lists (id, name, created_by, created_at, tombstone, rev, sort_order) "
        "VALUES (?, ?, ?, ?, 0, ?, ?)",
        (payload.id, payload.name, user.subject, db.stamp(), rev, sort_order),
    )
    return AppliedList(
        id=payload.id,
        name=payload.name,
        rev=rev,
        tombstone=False,
        sort_order=sort_order,
    )


def apply_delete_list(conn: sqlite3.Connection, list_id: str, user: AuthUser) -> AppliedList:
    """delete_list: tombstones the list + cascades tombstones to its live items,
    each item with its own rev so offline devices receive them via pull.
    Last live list → 409; already-deleted → no-op success; unknown → 400."""
    row = conn.execute("SELECT * FROM lists WHERE id = ?", (list_id,)).fetchone()
    if row is None:
        raise SyncError(f"unknown list: {list_id}", 400)
    if row["tombstone"]:
        return AppliedList(
            id=row["id"],
            name=row["name"],
            rev=row["rev"],
            tombstone=True,
            sort_order=row["sort_order"],
        )

    live_count = conn.execute("SELECT COUNT(*) FROM lists WHERE tombstone = 0").fetchone()[0]
    if live_count <= 1:
        raise SyncError("cannot delete the last list", 409)

    now = db.stamp()
    rev = db.next_rev(conn)
    conn.execute("UPDATE lists SET tombstone = 1, rev = ? WHERE id = ?", (rev, list_id))
    for item in conn.execute(
        "SELECT id FROM items WHERE list_id = ? AND tombstone = 0", (list_id,)
    ).fetchall():
        conn.execute(
            "UPDATE items SET tombstone = 1, updated_at = ?, rev = ? WHERE id = ?",
            (now, db.next_rev(conn), item["id"]),
        )
    return AppliedList(
        id=row["id"], name=row["name"], rev=rev, tombstone=True, sort_order=row["sort_order"]
    )


def _applied_list_row(row: sqlite3.Row) -> AppliedList:
    return AppliedList(
        id=row["id"],
        name=row["name"],
        rev=row["rev"],
        tombstone=bool(row["tombstone"]),
        sort_order=row["sort_order"],
    )


def apply_reorder_lists(
    conn: sqlite3.Connection, payload: list[str], user: AuthUser
) -> list[AppliedList]:
    """reorder_lists (feature 003): assigns sort_order = index for each id in the
    given sequence and bumps each row's rev via next_rev, so SSE/feeds and the
    pull watermark propagate the new order to every device.
    Unknown or tombstoned id → 400; validation happens BEFORE any write and the
    push transaction rolls back anyway, so a failure leaves the order untouched.
    Wire shape is FLAT: {"op": "reorder_lists", "order": [uuid, ...]} — the
    full desired sequence, validated here (non-empty, uuids, no duplicates)."""
    if not payload:
        raise SyncError("order: the full list sequence is required", 422)
    if len(set(payload)) != len(payload):
        raise SyncError("order: duplicate list ids", 422)
    for list_id in payload:
        try:
            uuid.UUID(list_id)
        except (ValueError, TypeError):
            raise SyncError(f"order: not a uuid: {list_id}", 422) from None
        row = conn.execute("SELECT * FROM lists WHERE id = ?", (list_id,)).fetchone()
        if row is None:
            raise SyncError(f"unknown list: {list_id}", 400)
        if row["tombstone"]:
            raise SyncError(f"list is deleted: {list_id}", 400)

    applied: list[AppliedList] = []
    for index, list_id in enumerate(payload):
        rev = db.next_rev(conn)
        conn.execute(
            "UPDATE lists SET sort_order = ?, rev = ? WHERE id = ?", (index, rev, list_id)
        )
        row = conn.execute("SELECT * FROM lists WHERE id = ?", (list_id,)).fetchone()
        applied.append(_applied_list_row(row))
    return applied


def apply_reorder_items(
    conn: sqlite3.Connection, list_id: str, order: Sequence[str], user: AuthUser
) -> list[Applied]:
    """reorder_items (feature 004): assigns sort_order = index for each item id
    in the given sequence and bumps each row's rev via next_rev, so SSE/feeds
    and the pull watermark propagate the new order to every device.
    Only ACTIVE, live items of THAT list may appear — unknown, tombstoned,
    foreign-list or ticked (completed/dropped) ids → 400; the push transaction
    rolls back anyway, so a failure leaves every order untouched.
    Wire shape is FLAT: {"op": "reorder_items", "list_id": uuid,
    "order": [uuid, ...]} — the client is the final authority and normally
    sends the complete active set; partial sequences are accepted and assign
    indices only to the listed ids (validation first: non-empty, uuids, no
    duplicates; 422)."""
    require_live_list(conn, list_id)  # unknown/tombstoned list → 400, batch rolls back
    if not order:
        raise SyncError("order: the active item sequence is required", 422)
    if len(set(order)) != len(order):
        raise SyncError("order: duplicate item ids", 422)
    for item_id in order:
        try:
            uuid.UUID(item_id)
        except (ValueError, TypeError):
            raise SyncError(f"order: not a uuid: {item_id}", 422) from None
        row = _fetch_row(conn, item_id)
        if row is None:
            raise SyncError(f"unknown item: {item_id}", 400)
        if row["tombstone"]:
            raise SyncError(f"item is deleted: {item_id}", 400)
        if row["list_id"] != list_id:
            raise SyncError(f"item {item_id} belongs to another list", 400)
        if row["status"] != "active":
            raise SyncError(f"item {item_id} is not active", 400)

    applied: list[Applied] = []
    for index, item_id in enumerate(order):
        rev = db.next_rev(conn)
        conn.execute(
            "UPDATE items SET sort_order = ?, rev = ? WHERE id = ?", (index, rev, item_id)
        )
        applied.append(_fetch_applied(_fetch_row(conn, item_id)))
    return applied


def push(conn: sqlite3.Connection, request: PushRequest, user: AuthUser) -> PushResponse:
    applied: list[Applied] = []
    applied_lists: list[AppliedList] = []
    trip_response = None
    conn.execute("BEGIN IMMEDIATE")
    try:
        db.upsert_user(conn, user.subject, user.name)
        rejected: list[RejectedMutation] = []
        for index, mutation in enumerate(request.mutations):
            # Per-mutation isolation (feature-004 fix): one poison mutation (e.g.
            # an upsert for a list tombstoned on another device) used to 400 the
            # WHOLE batch and retry forever. It's reported instead, the rest
            # applies, and the client drops the entry at `index` + beacons it.
            try:
                if mutation.op == "upsert_item":
                    assert mutation.item is not None  # enforced by the model validator
                    applied.extend(upsert_apply(conn, mutation.item, user))
                elif mutation.op == "create_list":
                    assert mutation.list is not None
                    applied_lists.append(apply_create_list(conn, mutation.list, user))
                elif mutation.op == "delete_list":
                    assert mutation.list_id is not None
                    applied_lists.append(apply_delete_list(conn, mutation.list_id, user))
                elif mutation.op == "reorder_lists":
                    assert mutation.order is not None
                    applied_lists.extend(apply_reorder_lists(conn, mutation.order, user))
                elif mutation.op == "reorder_items":
                    assert mutation.list_id is not None and mutation.order is not None
                    applied.extend(
                        apply_reorder_items(conn, mutation.list_id, mutation.order, user)
                    )
                else:
                    assert mutation.trip is not None
                    from . import trips as trips_mod

                    trip_response = trips_mod.apply_trip(conn, mutation.trip, user)
            except SyncError as exc:
                rejected.append(RejectedMutation(index=index, op=mutation.op, detail=exc.detail))
                print(
                    f"[push-rejected] index={index} op={mutation.op} {exc.detail}", flush=True
                )
            except Exception as exc:  # noqa: BLE001 — reject + log, never kill the batch
                rejected.append(
                    RejectedMutation(index=index, op=mutation.op, detail=f"internal error: {exc}")
                )
                print(f"[push-error] index={index} op={mutation.op} {exc!r}", flush=True)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return PushResponse(
        applied=AppliedGroup(items=applied, lists=applied_lists),
        trip=trip_response,
        rejected=rejected,
    )


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

    # contracts/api.md v2: watermark-0 snapshot includes ALL lists (tombstoned
    # ones too, so devices can retire their active selection).
    if watermark == 0:
        list_rows = conn.execute("SELECT * FROM lists ORDER BY rev").fetchall()
    else:
        list_rows = conn.execute(
            "SELECT * FROM lists WHERE rev > ? ORDER BY rev", (watermark,)
        ).fetchall()

    history = []
    if request.include_history:
        history = conn.execute(
            "SELECT * FROM history ORDER BY occurred_at DESC LIMIT 1000"
        ).fetchall()

    return PullResponse(
        watermark=current,
        lists=[_row_to_list(r) for r in list_rows],
        items=[_row_to_item(r) for r in rows],
        history=[_row_to_history(r) for r in history],
        full_snapshot=full_snapshot,
    )

"""T032 — history list + hard delete (FR-011: user-mandated permanence, no tombstone)."""

from __future__ import annotations

import sqlite3

from fastapi import HTTPException

from .models import HistoryOut, _row_to_history

HISTORY_LIMIT = 1000


def history_list(conn: sqlite3.Connection) -> list[HistoryOut]:
    """GET /api/v1/history → full history list (contracts/api.md), same payload as
    pull's `history`: newest first, capped at 1000 rows."""
    rows = conn.execute(
        "SELECT * FROM history ORDER BY occurred_at DESC LIMIT ?", (HISTORY_LIMIT,)
    ).fetchall()
    return [_row_to_history(r) for r in rows]


def history_delete(conn: sqlite3.Connection, history_id: str) -> None:
    """DELETE /api/v1/history/{id} → 204 hard delete; no tombstone (Constitution I exception)."""
    cursor = conn.execute("DELETE FROM history WHERE id = ?", (history_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="history entry not found")

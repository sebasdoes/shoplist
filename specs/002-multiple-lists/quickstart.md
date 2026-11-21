# Quickstart & Validation: Multiple lists

**Feature**: `002-multiple-lists`

## Prereqs

Same as feature 001 quickstart (`specs/001-mvp-synced-grocery-list/quickstart.md`). The server DB may be a copy of the real one for migration testing — back up `/opt/docker/shoplist/shoplist.db` BEFORE deploying v2.

## Validate

**Migration (SC-006)**: copy the live DB locally, open it with sqlite3, note item count. Run `uv run uvicorn app.main:app` once (migration runs). Verify: `PRAGMA user_version` = 2; `SELECT count(*) FROM items WHERE list_id='00000000-0000-4000-8000-000000000001'` == pre-count; rerun server → idempotent, no duplicate seeding.

**US1 — switch + per-list trips**: two browser windows. Both lists visible in drawer. Add item to list A and B (offline one of them). Trip done on A → A's items archive, A's staples re-add on A, B's list byte-identical. Restart → active list remembered.

**US2 — create/delete**: create "Winkelpad A" → syncs to window 2 within seconds (SSE push). Delete it → vanishes both sides, its items tombstoned. Delete the last remaining list → refused with message. Re-create the same name → allowed.

**US3 — global suggestions**: buy koffie on Boodschappen, open quick-add on Winkelpad A → koffie suggested there; re-adding creates it ON A.

**Offline**: airplane mode → switch lists (instant, local), create a list, add items — all queue and land atomically on reconnect.

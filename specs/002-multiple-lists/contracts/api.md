# API Contract deltas: Multiple lists

**Feature**: `002-multiple-lists` | **Base unchanged**: `https://shoplist.example.com/api/v1`

The push/pull shapes gain list support. Everything else (auth, LWW, dedupe, idempotent trips, SSE events) is unchanged.

## Push mutations (POST /sync/push)

Existing ops gain context; two new ops:

```json
{"mutations": [
  {"op": "create_list", "list": {"id": "uuid", "name": "Winkelpad A"}},
  {"op": "delete_list", "list_id": "uuid"}
]}
```

- `upsert_item` items now MUST carry `list_id` (uuid of a live list; 400 + whole-batch rollback otherwise).
- `create_list`: server normalizes/validates the name (trim, 1–100, 422 on violation). Tombstoned names may be reused (names are not unique). `applied` gains a `lists` section: `[{"id", "name", "rev", "tombstone": false}]`.
- `delete_list`: tombstones the list + cascades tombstones to all its live items. Deleting the LAST live list → `409 {"detail": "cannot delete the last list"}`. Deleting an already-deleted list → no-op success.
- `trip` op gains `list_id` (uuid); trip decisions resolve items WITHIN that list only. Decisions referencing items from another list → 400, whole transaction rolls back. Duplicate trip id stays a no-op (`duplicate: true`).

## Pull (POST /sync/pull)

Response gains `lists`:

```json
{"watermark": 142, "lists": [{"id": "...", "name": "Boodschappen", "tombstone": false, "rev": 90}],
 "items": [...],
 "history": [...], "full_snapshot": false}
```

- `watermark: 0` snapshot includes ALL lists (tombstoned ones too, so devices can retire their active selection).
- Items in the response carry `list_id`.

## Client sync loop delta

- Pull applies `lists` into the `lists` store; when the stored `state.active_list` is tombstoned or missing → fall back to the first live list (FR-020).
- The quick-add drawer's active-name suppression uses the ACTIVE list only; history pool stays global.

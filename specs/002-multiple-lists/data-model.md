# Phase 1 Data Model: Multiple lists

**Feature**: `002-multiple-lists` | **Date**: 2026-11-21 | **Schema v2**

SQLite migration v1→v2 (one-shot, server-side, guarded by `PRAGMA user_version`):

1. `CREATE TABLE lists (id TEXT PK, name TEXT NOT NULL, created_by TEXT NULL, created_at TEXT NOT NULL, tombstone INTEGER NOT NULL DEFAULT 0, rev INTEGER NOT NULL)`
2. Seed default list: fixed uuid `00000000-0000-4000-8000-000000000001`, name `Boodschappen`, created_by NULL, created_at = migration time.
3. `ALTER TABLE items ADD COLUMN list_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'` (all existing items → Boodschappen)
4. `ALTER TABLE items ... recreate` — replace the partial unique index `UNIQUE(name_key) WHERE status='active' AND tombstone=0` with `UNIQUE(list_id, name_key) WHERE status='active' AND tombstone=0`
5. `ALTER TABLE trips ADD COLUMN list_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'`
6. `ALTER TABLE history ADD COLUMN list_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'`
7. `PRAGMA user_version = 2`

## Tables

### lists

| column | type | notes |
|---|---|---|
| id | TEXT uuid PK | |
| name | TEXT NOT NULL | trimmed, ≤100 chars, NOT unique |
| created_by | TEXT NULL | subject id of creator (NULL for migrated default) |
| created_at | TEXT NOT NULL | server-stamped |
| tombstone | INTEGER NOT NULL DEFAULT 0 | deleting a list tombstones it |
| rev | INTEGER NOT NULL | global sync revision |

### items / trips / history

Gain `list_id TEXT NOT NULL` as above. Semantics:

- items: names are unique per (list_id, name_key) among active, live rows. Dedupe merge folds same-name pushes onto the survivor OF THE SAME LIST.
- trips: trip decisions apply only to the list's own items; "kept" bulk applies to remaining active items OF THAT LIST.
- history: list_id informative (which list it came from); the suggestion pool is global — rankSuggestions consumes ALL history rows regardless of list.
- staple re-adds (trip complete) create the new active row on the SAME list as the completed item, prefilled from the history snapshot.

Deletion cascade: `delete_list` tombstones the list row and tombstones every live item of that list (each item gets its own tombstone rev so offline devices receive them via the normal watermark pull).

## Client (IndexedDB)

- New store `lists` (keyPath `id`).
- `state.active_list` (device-persisted selection; defaults to the first non-tombstoned list).
- Pull upserts `lists` alongside items/history — the watermark mechanism is unchanged (rev is global across tables); a fresh device snapshot includes lists.

## Validation

- list name: required after trim, 1–100 chars (client + server identical)
- item create requires a live (non-tombstoned) list_id; unknown/tombstoned list_id → 400 and the whole push transaction rolls back (same atomicity as unknown item in trip decisions)

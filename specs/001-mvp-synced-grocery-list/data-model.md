# Phase 1 Data Model: Synced offline grocery list (MVP)

**Feature**: `001-mvp-synced-grocery-list` | **Date**: 2026-11-21

SQLite (WAL mode) on the server; mirrored subset in IndexedDB on the client. All timestamps UTC ISO-8601, stamped by the server on receipt. All rows carry `rev INTEGER` from a single monotonic sequence (the sync watermark source).

## Tables

### items

| column | type | notes |
|---|---|---|
| id | TEXT uuid PK | client-generated for local adds |
| name | TEXT NOT NULL | display form, trimmed, ≤200 chars |
| name_key | TEXT NOT NULL | normalized (see research D6); display/search helper only — NOT unique (v1.3.0) |
| quantity | TEXT NULL | ≤50 chars, e.g. "2x", "500g" |
| note | TEXT NULL | ≤200 chars |
| status | TEXT NOT NULL | active / completed / dropped — see transitions |
| staple | INTEGER NOT NULL DEFAULT 0 | survives trips and manual deletion |
| created_by | TEXT NOT NULL | Pocket ID subject id |
| created_at | TEXT NOT NULL | server-stamped |
| updated_at | TEXT NOT NULL | server-stamped; LWW timestamp source |
| rev | INTEGER NOT NULL | global monotonic sync revision |
| tombstone | INTEGER NOT NULL DEFAULT 0 | 1 = deleted, propagates to offline devices |

Unique (partial): `UNIQUE(name_key) WHERE status='active' AND tombstone=0`.

> **Amendment (constitution v1.3.0, 2026-11-21)**: this unique-partial index was removed (dedupe index dropped, schema v5). Identical names now coexist on a list as independent rows; `name_key` remains only as a normalized display/search helper — the index above is historical (v1 schema).

### history

| column | type | notes |
|---|---|---|
| id | TEXT uuid PK | |
| item_id | TEXT NULL | link to originating item, nullable |
| name | TEXT NOT NULL | snapshot at trip time |
| quantity | TEXT NULL | snapshot |
| note | TEXT NULL | snapshot |
| outcome | TEXT NOT NULL | bought / not_bought |
| staple_snapshot | INTEGER NOT NULL DEFAULT 0 | for staple re-add prefill |
| actor | TEXT NOT NULL | who completed the trip |
| occurred_at | TEXT NOT NULL | trip completion time |
| rev | INTEGER NOT NULL | |

Deletion of a history entry (the X) is a HARD delete — user-mandated permanence; it does not travel as a tombstone, devices drop it from suggestions locally on pull of the history list. (Constitution I tombstone rule applies to list items; history X is an explicit user-approved exception recorded here and in FR-011.)

### trips

| column | type | notes |
|---|---|---|
| id | TEXT PK | client-generated, idempotency key |
| completed_by | TEXT NOT NULL | |
| completed_at | TEXT NOT NULL | server-stamped |
| rev | INTEGER NOT NULL | |

Per-trip item decisions are effects of the transaction (history rows, item status flips, staple re-adds), not a stored decisions blob.

### users

| column | type | notes |
|---|---|---|
| subject | TEXT PK | Pocket ID subject id (sub claim) |
| display_name | TEXT NOT NULL | set/refreshed when first seen in a token |

### sync_state (client only, IndexedDB)

watermark (last pulled rev), outbox queue (mutations or trip events, with client ids), token cache.

## Item status transitions

```text
                ┌──────────── add (or staple re-add) ────────────┐
                ▼                                                │
  [none] ──► active ──(trip done: bought)──► completed           │
                │                                                │
                ├──(trip done: keep)───────► active (unchanged) ──┘
                ├──(trip done: drop)───────► dropped
                └──(manual delete, any time)► tombstone=1
```

- `completed` items never return to `active`; history re-adds and staple re-adds create NEW active rows (fresh ids), with the old row untouched for audit.
- `dropped` = "not bought, user chose drop"; produces a `not_bought` history entry.
- Manual delete of an active item: tombstone, keeps staple flag so re-add intent is not silently lost.
- Re-add from quick-add history: creates a new active row with quantity/note prefilled from the history snapshot.

## Validation rules (identical client + server)

- name: required after trim, 1–200 chars
- quantity: ≤50 chars; note: ≤200 chars
- trip id: non-empty uuid format
- unknown status/outcome values: rejected 422

## Indexes

- items: `(status, tombstone, name_key)` unique-partial *historical — dropped in schema v5 (constitution v1.3.0); `name_key` is now an ordinary index serving display/search (type-to-search + quick-add aggregation)* + `(rev)` for pull
- history: `(occurred_at DESC)` + `(rev)`

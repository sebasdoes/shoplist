# Tasks: Multiple lists

**Input**: design docs from `/specs/002-multiple-lists/`; prior feature 001 (001-mvp-synced-grocery-list) remains the base reference for unchanged mechanics.

**Tests**: TDD for server migration, cascade, and trip scoping (constitution applies: sync-adjacent logic is failure-prone). UI verified per feature-001 quickstart patterns (two-device flow, Playwright forge repro).

## Phase 1: Server

- [ ] T001 [P] Write failing tests: migration v1→v2 creates `lists`, seeds `Boodschappen` (fixed uuid 00000000-...-0001), backfills items/trips/history `list_id`, replaces the unique index with `(list_id, name_key)` on active rows, sets `user_version=2`, is idempotent (`server/tests/test_migration2.py`)
- [ ] T002 Implement the migration runner upgrade in `server/app/db.py` (user_version gate; keep the rev sequence untouched)
- [ ] T003 [P] Write failing tests for list ops: `create_list` (validation 422, name reuse allowed), `delete_list` cascade tombstones items, last-list 409, tombstoned-list item-push 400 + rollback, `trip` op with `list_id` scoping + cross-list decision 400 (`server/tests/test_lists.py`)
- [ ] T004 Implement list ops in `server/app/models.py` + `server/app/sync.py`: create_list / delete_list ops, per-list dedupe merge, trip scoping, cascade; pull response `lists[]`
- [ ] T005 Contract tests: pull snapshot includes lists; push response `applied.lists` (`server/tests/test_api.py` additions)

## Phase 2: Client

- [ ] T006 [P] IndexedDB v2 in `frontend/src/lib/db.ts`: `lists` store; migrate existing local IDBs (they only hold mirrored subsets — simplest: bump version, clear + resync on version change)
- [ ] T007 [P] `frontend/src/lib/stores.ts`: lists store, activeList state (persist in `state.active_list`, fallback per FR-020), filter `$activeItems` by active list, addItem gains `list_id`, trip/bump/staple/scoping per list
- [ ] T008 Write failing Vitest first: switching lists renders that list's items from IndexedDB only; trip on list 1 doesn't touch list 2 (`frontend/tests/lists.test.ts`)
- [ ] T009 Implement `frontend/src/components/ListMenu.svelte` (hamburger drawer: list names, active highlight, "+ New list" input, per-list delete with undo snackbar, last-list refusal) and wire the header basket icon as the menu toggle
- [ ] T010 Quick-add: suppress only names active on the CURRENT list; keep the global history pool (QuickAdd/rank unchanged otherwise)
- [ ] T011 Outbox: create/delete list mutations ride the normal outbox; offline switch works (list-switching is IndexedDB-only)

## Phase 3: Integration & ship

- [ ] T012 Full validation: fresh install + migrated-install paths (playwright forge + real two-device), SC-006 data-loss check on the live DB backup before deploy
- [ ] T013 Commit, push, CI green, PR, deploy per feature-001 flow

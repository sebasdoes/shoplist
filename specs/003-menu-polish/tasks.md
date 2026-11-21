# Tasks: Menu/header polish + list reordering

**Feature**: `003-menu-polish` (spec delta on 002; constitution v1.2.0 amendment covers reordering)

## Decisions (stakeholder, 2026-11-21)

1. User name + Sign out move INTO the hamburger drawer (account section at the drawer top).
2. Header shows the ACTIVE LIST NAME beside the basket (menu) button — not the user name.
3. Lists can be reordered via up/down arrows per row in the drawer; order is household-wide (server-side `sort_order`, boring LWW) and syncs through the outbox like everything else.
4. `.empty-hint` on the list view gets top padding so "Nothing here yet" stops hugging the add bar.

## Server (schema v3)

- [ ] T001 [P] Failing test first: migration v2→v3 adds `lists.sort_order` (default 0), assigns sequential order by `created_at`, `user_version=3`, idempotent (`server/tests/test_migration3.py`)
- [ ] T002 Implement the v3 migration + fresh-install schema gains `sort_order` directly
- [ ] T003 [P] Failing tests: `reorder_lists` op — sets sequential sort_orders, bumps per-row revs (SSE/pull propagate), unknown id → 400 + rollback; `create_list` assigns max+1; pull/push list rows carry `sort_order` (`server/tests/test_lists.py` additions)
- [ ] T004 Implement: models (ReorderListsPayload, ListOut/AppliedList + sort_order), sync.apply_reorder_lists, create_list max+1

## Frontend

- [ ] T005 [P] Vitest first: drawer shows user + Sign out (calls the signout hook), rows in sort_order, up/down buttons reorder optimistically (IndexedDB immediate) and enqueue `reorder_lists`; pull adopts `sort_order` (`frontend/tests/menu.test.ts` additions or new file)
- [ ] T006 Implement: `db.ts` List + `sort_order`; pull/flush adoption; `stores.reorderLists(orderedIds)` optimistic outbox mutation
- [ ] T007 `ListMenu.svelte`: account section (user name + Sign out button wired to a passed `onsignout` prop), rows sorted by (sort_order, created_at), ↑/↓ buttons per row
- [ ] T008 `App.svelte`: header = [basket menu button][active list name]; remove name/Sign out from the header; wire `onsignout`
- [ ] T009 `app.css`: `.empty-hint { margin-top: 1.4rem; color: var(--muted); }` plus drawer account-section styles

## Integration

- [ ] T010 Full suites green (server pytest, frontend vitest+tsc+build), deploy flow with fresh screenshots for the stakeholder

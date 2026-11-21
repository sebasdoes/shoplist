# Tasks: Item reordering within a list

**Feature**: `004-item-reorder` (schema v4; follows 002/003 patterns)

## Stakeholder ask (2026-11-21)

"I'd like to reorder items in the list." Scope decisions (agent-inherited patterns, boring-by-design):

- Ordering exists for the ACTIVE items of a list — the shopping queue. Ticked items stay grouped after unticked ones, unchanged.
- Schema v4: `items.sort_order INTEGER NOT NULL DEFAULT 0`; backfill active rows sequentially (newest first — matches today's display order). Migration must LIFT sync_meta above max data rev BEFORE bumping (v2/v3 lesson) so devices already past the watermark receive the order.
- New contract op: `reorder_items` {list_id, order:[item ids]} — FLAT wire shape (feature-003 lesson: no nested payloads, pydantic `list[...]` annotation trap in classes with a sibling named `list` — use Sequence). Only ACTIVE, live, same-list item ids; unknown/tombstoned/foreign-list → 400 + rollback; sequence must be complete for the list (missing ids keep position, validated client-side; server accepts partial but assigns indices only to listed ids and leaves others — final authority is the client always sending the full active set). Each assigned row bumps rev (SSE/pull propagate).
- New ACTIVE items (add, staple re-add, history re-add) enter at the TOP: sort_order = min(active sort_orders of that list) − 1.
- Client: drag handle (⠿ grip) left of each unticked row; pointer-drag to reorder with live preview; touch AND mouse must work; must not conflict with existing long-press-on-row (edit) or tick interactions. Client-side persistence to IndexedDB BEFORE network; one `reorder_items` outbox mutation after drag end; optimistic order adopted from server echo.
- Tick behavior unchanged: ticking hides the row from the unticked queue (moves into the ticked group) — its sort_order stays unused while ticked.

## Tasks

- [ ] T001 [P] Server tests FIRST: v4 migration (column, sequential backfill, sync_meta lift + rev bumps, user_version=4, idempotent) (`server/tests/test_migration4.py`)
- [ ] T002 Implement v4 migration + fresh-install schema (sort_order on items; new items helpers: top-insert helper for add/staple-re-add/re-add paths)
- [ ] T003 [P] Server tests FIRST: `reorder_items` op — valid full reorder (sequential assignment + rev bumps), foreign/unknown/tombstoned item 400 + rollback, empty order 422, duplicates 422, partial order accepted with listed-ids-only reindex (`server/tests/test_items_reorder.py`)
- [ ] T004 Implement models + sync (op + validation), plus item create/top-insert in stores-side server logic (upsert new row → min-1; staple re-add → min-1)
- [ ] T005 [P] Vitest FIRST: drag-reorder updates IndexedDB immediately, enqueues one reorder_items op after pointer release, order survives reload, server echo adopted (`frontend/tests/item-reorder.test.ts`)
- [ ] T006 Implement handle + pointer-drag reorder in ItemRow/App list (choose svelte-dnd-action OR manual pointer math; touch + mouse; no conflict with long-press edit); top-insert for adds
- [ ] T007 Integration: suites green, two-device sync check (order propagates via SSE/pull), deploy with fresh screenshot

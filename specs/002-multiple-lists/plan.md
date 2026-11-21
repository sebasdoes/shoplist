# Implementation Plan: Multiple lists

**Branch**: `002-multiple-lists` | **Date**: 2026-11-21 | **Spec**: [spec.md](spec.md)

## Summary

Per-list lists for a household: schema v2 adds a `lists` table and `list_id` to items/trips/history; the PWA gains a hamburger drawer (switch, create, delete) and per-list scoping of trips/staples; the quick-add suggestion pool stays household-global. Offline-first, outbox, LWW, idempotent trips and the SSE push channel are all unchanged mechanics with scoping added.

## Technical Context

Unchanged from feature 001 (Python 3.12 + FastAPI + SQLite WAL server; Svelte 5 + Vite + IndexedDB PWA; single container). New: schema `user_version` 2 migration (one-shot), `lists` IndexedDB store + persisted active-list selection.

## Constitution Check

| Principle | Status |
|---|---|
| I. No data loss | PASS — migration guarded + tested (SC-006); deletions are tombstones; history survives list deletion |
| II. Offline-first | PASS — list switching is IndexedDB-only; list ops ride the outbox |
| III. Lists multiply (amended v1.1.0) | IN SCOPE by amendment; aisles/recipes/sharing/reminders still rejected |
| IV. Boring conflicts | PASS — same LWW + dedupe, now scoped per list; trip idempotency unchanged |
| V. Zero user management | PASS — unchanged |
| VI. Frugal self-hosting | PASS — one table, one column per table, no new services |
| VII. Queue-style UI | PASS — drawer is a menu, not a dashboard |

**Post-design re-check**: no violations. Complexity tracking table empty.

## Project Structure (delta)

```text
server/app/db.py        # + migration v1->v2 (user_version gate), idempotent
server/app/models.py    # + create_list/delete_list ops, list_id on upsert/trip
server/app/sync.py      # + list ops, per-list dedupe, cascade, trip scoping, pull lists[]
server/tests/           # + test_migration2.py, test_lists.py, test_events stays
frontend/src/lib/db.ts      # + lists store, IDB version bump (resync on change)
frontend/src/lib/stores.ts  # + lists store, activeList persistence, scoping, bumpQuantity
frontend/src/lib/quantity.ts # pure stepper logic (already in main)
frontend/src/components/ListMenu.svelte # hamburger drawer
frontend/tests/lists.test.ts  # switch/trip-scope/isolation (TDD)
```

**Structure Decision**: monorepo unchanged; both workstreams stay disjoint (server/ vs frontend/).

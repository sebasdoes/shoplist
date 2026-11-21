# Implementation Plan: Synced offline grocery list (MVP)

**Branch**: `001-mvp-synced-grocery-list` | **Date**: 2026-11-21 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-mvp-synced-grocery-list/spec.md`

## Summary

A shared household grocery list PWA (User A + User B) that works fully offline and syncs through a small self-hosted FastAPI server on the VPS. Items carry name/quantity/note and a staple flag; "Trip done" archives purchases to history, resolves unticked items with one-tap keep/drop, and re-adds staples with their last quantity/note. Auth is Pocket ID (OIDC public client, PKCE); the server validates JWTs via JWKS. Conflicts resolve with per-field last-write-wins on server-received timestamps and dedupe on normalized names.

## Technical Context

**Language/Version**: Python 3.12 (FastAPI server); TypeScript via Vite + Svelte 5 (PWA)

**Primary Dependencies**: FastAPI, PyJWT, httpx (JWKS fetch), uvicorn — server; Svelte, vite-plugin-pwa, idb — client

**Storage**: SQLite (WAL) server-side; IndexedDB client-side (mirrored subset + outbox + watermark)

**Testing**: pytest (server: sync engine, dedupe, LWW, trip transactions); Vitest + Playwright-lite device-flow checks (client); manual two-browser airplane-mode validation per quickstart.md

**Target Platform**: Linux server (VPS, podman/container behind Caddy); Android Chrome PWA clients

**Project Type**: web application (monorepo: `server/` + `frontend/`)

**Performance Goals**: offline interaction < 50 ms perceived; sync round trip < 10 s after reconnect (SC-002)

**Constraints**: < 50 MB server idle RAM (SC-005); zero data loss on any client (Constitution I); full function with no connectivity (Constitution II)

**Scale/Scope**: 2 users, 1 list, ≤ a few thousand history rows — no tenancy, no queues, no cache layers

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. No data loss | PASS | mutations persist to IndexedDB before network (FR-003); tombstones (FR-012); outbox retains failed pushes (FR-014). History X-delete is the one documented exception (hard delete, user-mandated, FR-011) |
| II. Offline-first | PASS | all reads/writes local-first; sync best-effort on reconnect (FR-004, FR-013) |
| III. Scope frozen | PASS | one list; rejected features named in constitution; staple re-add in scope by explicit decision |
| IV. Boring conflicts | PASS | per-field LWW + server timestamps (FR-007); trip = one idempotent transaction (FR-009); no CRDT |
| V. Zero user management | PASS | OIDC public client PKCE; server verifies JWT via JWKS (FR-005, FR-006); no passwords stored |
| VI. Frugal self-hosting | PASS | FastAPI+SQLite monolith, single container, GHCR public image, compose + Caddy delivery |
| VII. Queue-style UI | PASS | add bar → list → history drawer; no settings beyond sign-out |

**Post-design re-check**: no violations introduced — data-model and contracts stay within the above (complexity table below is empty).

## Project Structure

### Documentation (this feature)

```text
specs/001-mvp-synced-grocery-list/
├── plan.md              # This file (/speckit-plan command output)
├── spec.md              # User stories + requirements (approved contract)
├── research.md          # Phase 0 output: OIDC verification, sync model, storage, ranking
├── data-model.md        # Phase 1 output: tables, transitions, validation, indexes
├── quickstart.md        # Phase 1 output: run + validation commands
├── contracts/
│   └── api.md           # Phase 1 output: REST sync contract + client sync loop
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
server/
├── app/
│   ├── main.py            # FastAPI app, routers, health
│   ├── config.py          # env: OIDC_ISSUER, OIDC_AUDIENCE, DB path
│   ├── auth.py            # JWKS cache + JWT verification dependency
│   ├── db.py              # SQLite (WAL), migrations, rev sequence
│   ├── models.py          # pydantic request/response schemas
│   ├── sync.py            # push/pull: LWW, dedupe, tombstones
│   ├── trips.py           # idempotent trip transaction (history, keep/drop, staple re-add)
│   └── history.py         # history list + hard delete
└── tests/
    ├── test_sync.py       # LWW per field, dedupe merge, tombstone precedence, out-of-order
    ├── test_trips.py      # idempotency, atomicity, staple re-add, keep/drop
    ├── test_auth.py       # JWT validation, 401 paths
    └── test_api.py        # contract-level, matches contracts/api.md

frontend/
├── src/
│   ├── lib/
│   │   ├── db.ts          # IndexedDB schema + wrapper (items, outbox, state)
│   │   ├── sync.ts        # sync loop per contracts/api.md
│   │   ├── oidc.ts        # PKCE login, token cache, silent refresh
│   │   └── stores.ts      # Svelte stores fed from IndexedDB
│   ├── App.svelte
│   ├── components/        # AddBar, ItemRow, ItemEditSheet, TripDoneSheet, HistoryDrawer, QuickAdd
│   └── main.ts
├── public/                # manifest, icons, service worker (vite-plugin-pwa)
└── tests/                 # Vitest unit tests for db + sync + oidc

deploy/                    # delivered to User A, never executed by agent on the VPS
├── compose.shoplist.yaml
└── Caddyfile.snippet
```

**Structure Decision**: Monorepo with `server/` and `frontend/` — one repo, one GHCR image for the server, the frontend is a static bundle served BY the FastAPI app (single container, no separate image). `deploy/` holds only the file set the user applies. No shared code exists between the two, so no monorepo tooling.

## Complexity Tracking

> No violations to justify — table intentionally empty.

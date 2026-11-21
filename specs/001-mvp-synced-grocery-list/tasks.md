# Tasks: Synced offline grocery list (MVP)

**Input**: Design documents from `/specs/001-mvp-synced-grocery-list/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/api.md, quickstart.md, constitution

**Tests**: Included per constitution workflow rule — sync engine, dedupe, and trip transitions get pytest suites written before their implementations (test-driven where it counts). UI verified per quickstart.md.

**Path conventions**: monorepo per plan.md — `server/` (FastAPI) and `frontend/` (Svelte PWA). No shared code.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependencies)
- **[Story]**: owning user story, e.g. [US1]
- Field constraints cite `data-model.md` `Entity.field`; do not restate them

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: monorepo skeleton, toolchains, linting

- [ ] T001 Create monorepo skeleton per plan.md structure: `server/`, `frontend/`, `deploy/` with empty package scaffolding and `.gitignore` (python + node entries)
- [ ] T002 Initialize Python project at `server/pyproject.toml` with uv: fastapi, uvicorn[standard], pyjwt[crypto], httpx, pytest, ruff; Python 3.12 pinned
- [ ] T003 [P] Initialize frontend at `frontend/` with Vite + Svelte 5 + TypeScript: vite-plugin-pwa, idb, vitest; add eslint + prettier config
- [ ] T004 [P] Add ruff config to `server/pyproject.toml` (line 100, basic lint set) and prettier+eslint setup in `frontend/package.json`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: auth, DB, offline shell — must exist before any user story

- [ ] T005 Implement `server/app/config.py`: env loading for `OIDC_ISSUER`, `OIDC_AUDIENCE`, `SHOPLIST_DB` (default `/data/shoplist.db`), `SERVE_FRONTEND_DIR` (optional static dir); gitignored `.env` support
- [ ] T006 [P] Implement `server/app/auth.py`: JWKS fetch+cache (1 h TTL, refresh on unknown kid) and FastAPI dependency verifying RS256 / iss / aud / exp per research.md D7; reject missing/invalid → 401
- [ ] T007 [P] Write auth tests `server/tests/test_auth.py` FIRST (invalid sig, wrong aud, expired, missing header → 401; valid token → 200; mock JWKS with generated RSA pair), confirm they fail, then make T006 pass
- [ ] T008 Implement `server/app/db.py`: SQLite WAL connection helper, migration runner (schema v1 = items + history + trips + users tables exactly per data-model.md), global monotonic rev sequence, helper `stamp()` for server timestamps
- [ ] T009 [P] Implement `frontend/src/lib/oidc.ts`: PKCE authorization-code flow (S256, code_verifier stored on login start), scopes `openid profile email offline_access`, token cache in IndexedDB, silent refresh via refresh token, 401-triggered re-auth hook
- [ ] T010 [P] Implement `frontend/src/lib/db.ts`: IndexedDB schema per data-model.md client sync_state — stores `items`, `history`, `outbox` (mutations or trip events with client uuids), `state` (watermark, tokens)
- [ ] T011 [P] Implement `frontend/src/lib/api.ts`: fetch wrapper with base URL env, Authorization header injection, typed responses matching contracts/api.md, 401 → invoke T009 re-auth
- [ ] T012 Wire `frontend/src/main.ts` + `App.svelte` as the app shell (no features yet): loads db, attempts silent auth, renders placeholder; configure vite-plugin-pwa precache (manifest name "Shoplist", standalone, maskable icons) and NO API-response caching per contracts/api.md PWA surface
- [ ] T013 Add static-bundle serving to `server/app/main.py` via `SERVE_FRONTEND_DIR` (SingleFileResponse fallback to index.html, /api routes take precedence) + `GET /health` unauthenticated `{"ok": true}`; write `server/tests/test_api.py::test_health`
- [ ] T014 [P] Write `server/Dockerfile` (multi-stage uv, non-root uid 1000? NO — document choice: uv official image, run as non-root, /data volume for SQLite) and `.dockerignore`

**Checkpoint**: `uv run pytest -q` passes (auth + health), `npm run dev` shows the shell and logs in through Pocket ID dev client

---

## Phase 3: User Story 1 — Add items to a shared list that syncs (P1) 🎯 MVP

**Goal**: two users, one truthful list, offline add + auto-sync + dedupe

**Independent Test**: quickstart.md "US1 — shared synced list" steps 1–6

### Tests for US1 (write FIRST — TDD required by constitution)

- [ ] T015 [P] [US1] Write `server/tests/test_sync.py` (failing): per-field LWW resolution (research D2: quantity edit vs note edit both survive), auto-dedupe on normalized `name_key` (data-model items.name_key rule), tombstone precedence over same-epoch edits, out-of-order push safety, pull watermark incremental + full-snapshot flag at >500 rows (data-model rev field), server-stamped timestamps (client clock ignored)
- [ ] T016 [P] [US1] Write `server/tests/test_api.py` contract cases: POST /sync/push happy path + `merged_into` response (contracts/api.md), POST /sync/pull shapes, 401 without token, 422 on empty name (data-model.name validation "required after trim, 1–200 chars")

### Implementation for US1

- [ ] T017 [US1] Implement `server/app/models.py`: pydantic schemas for push/pull/history payloads exactly matching contracts/api.md (UpsertItem, TripMutation, PushRequest, PushResponse.applied + trip + merged_into, PullRequest/Response)
- [ ] T018 [US1] Implement `server/app/sync.py`: push transaction (in-order upsert_apply with LWW field-set resolution, dedupe merge onto survivor row, tombstone apply) and pull (rev-gated, snapshot flag), `name_key` normalization per research D6 (casefold + trim + collapse whitespace + NFKD diacritic strip)
- [ ] T019 [US1] Wire routers `POST /api/v1/sync/push`, `POST /api/v1/sync/pull` in `server/app/main.py` with auth dependency; make T015/T016 pass
- [ ] T020 [US1] Implement `frontend/src/lib/sync.ts`: outbox flush → push (adopt `applied`, handle `merged_into`, clear pushed) → pull watermark upsert → persist; triggers per contracts/api.md client sync loop (online event, focus, 60 s visible timer, debounced 2 s post-mutation); failure retains outbox + quiet "syncing…" chip, never an error dialog (FR-014)
- [ ] T021 [US1] Implement `frontend/src/lib/stores.ts` + `frontend/src/components/AddBar.svelte` + minimal `ItemRow.svelte`: list renders from IndexedDB only (no network read path), add bar prepends item locally + queues mutation; write `frontend/tests/sync.test.ts` (outbox ordering, offline queue, dedupe adoption of remote id)
- [ ] T022 [US1] Two-sign-in support: store + display name from token claims (users table upsert on first-seen per data-model users)

**Checkpoint**: quickstart US1 steps 1–6 pass in two browser windows; all server tests green

---

## Phase 4: User Story 2 — Finish shopping and restock staples (P1)

**Goal**: trip done = one atomic idempotent event: archive bought, keep/drop unticked, re-add staples with last quantity/note

**Independent Test**: quickstart.md "US2 — trip done + staples" steps 1–5

### Tests for US2 (write FIRST — TDD required by constitution)

- [ ] T023 [P] [US2] Write `server/tests/test_trips.py` (failing): idempotent duplicate trip id → `{"duplicate": true}` no effects (contracts/api.md trip semantics), atomicity (partial failure leaves no history rows), bought→history snapshot rows (data-model history fields), drop→`not_bought` history + item `dropped` status (data-model item status transitions), keep→still active, staple re-add creates NEW active row with staple flag + quantity/note prefilled from `history.staple_snapshot`/`history.quantity`/`history.note` (data-model), staple already-active → no duplicate, offline-trip-single-unit (whole trip mutation batch applies or nothing)

### Implementation for US2

- [ ] T024 [US2] Implement `server/app/trips.py`: `apply_trip()` single-transaction per data-model transitions; trip id = idempotency key (trips table); make T023 pass
- [ ] T025 [US2] Trip op inside POST /sync/push (contracts/api.md mutation `op: "trip"` with decisions array) — integration into sync.py from T018
- [ ] T026 [US2] Implement `frontend/src/components/TripDoneSheet.svelte`: press Trip done → collect ticked items; unticked items presented one-by-one for single-tap keep/drop (US2-2); whole trip queued as ONE outbox unit; silent idempotent retry on sync (US2-5)
- [ ] T027 [US2] ItemRow check-off interaction: local tick persists to IndexedDB immediately (Constitution I), queue mutation; ticked items visually separate on list
- [ ] T028 [US2] History foundation for UI: history rows from pull land in IndexedDB `history` store (server side already produces them via T024)

**Checkpoint**: quickstart US2 passes; duplicate-trip and offline-trip cases green in tests

---

## Phase 5: User Story 3 — Quantity, note, and staple flag per item (P2)

**Goal**: qualifying details on items, edited in one sheet, surviving per-field conflicts

**Independent Test**: quickstart.md "US3 — per-field conflicts" + staple-persistence checks

- [ ] T029 [P] [US3] Implement `frontend/src/components/ItemEditSheet.svelte`: edit name/quantity/note with client-side validation mirroring data-model constraints (name 1–200 required-after-trim, quantity ≤50, note ≤200), staple toggle; save → local upsert + outbox mutation
- [ ] T030 [P] [US3] Wire edit entry point in `ItemRow.svelte` (swipe or long-press → sheet) and render quantity/note inline per spec US3-1
- [ ] T031 [US3] Add `frontend/tests` case: per-field edit merge scenario replicated against mock push responses (US3-2 both edits survive)

**Checkpoint**: US3 scenarios from quickstart pass; staple flag survives trips and manual deletion (server tests already assert via T023/T015)

---

## Phase 6: User Story 4 — Quick-add from purchase history (P3)

**Goal**: history drawer with decayed-frequency ranking, tap-to-re-add, permanent X delete

**Independent Test**: quickstart.md "US4 — quick-add" steps 1–3

- [ ] T032 [P] [US4] Implement `server/app/history.py`: GET /api/v1/history (full list, payload per contracts/api.md) and DELETE /api/v1/history/{id} → 204 hard delete (FR-011, the Constitution-I exception); tests in `server/tests/test_history.py`
- [ ] T033 [US4] Implement `frontend/src/components/HistoryDrawer.svelte` + `QuickAdd.svelte`: ranking per research D4 `score = Σ 0.5^(days_since/14)` computed client-side over `history` store; tap → new active row with prefilled quantity/note + outbox mutation; X → optimistic remove + undo snackbar (5 s) → DELETE call (US4-2); exclude not_bought entries from suggestions
- [ ] T034 [US4] Re-add path: tapping a suggestion reuses T021 add pipeline with prefills; server-side nothing new (history re-add = normal item add per data-model transitions)

**Checkpoint**: US4 quickstart passes

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: deploy readiness, docs, secrets

- [ ] T035 [P] Write `deploy/compose.shoplist.yaml` (GHCR image ghcr.io/sebasdoes/shoplist-server, read-only rootFS, /data volume, unpublished port, joins `proxy` network) + `deploy/Caddyfile.snippet` (reverse_proxy to shoplist container at https://shoplist.v.akiha.nl) with apply instructions for Sebastiaan
- [ ] T036 [P] Secrets scan: `gitleaks`/grep pass over tree AND `git log -p` history before any further push; record result
- [ ] T037 [P] README.md: what it is, stack, dev quickstart (link quickstart.md), deploy instructions (link deploy/), OIDC client note
- [ ] T038 Create production Pocket ID client via API (X-API-Key): name "Shoplist", callbacks `https://shoplist.v.akiha.nl` + `http://localhost:5173`, isPublic, pkce, skipConsent; store client_id (the UUID) in deploy notes as OIDC_AUDIENCE
- [ ] T039 Run full quickstart.md validation end-to-end (all US checkpoints) and verify SC-005 (server idles < 50 MB) via `docker stats`; fix what fails
- [ ] T040 Final commit + push branch; open PR main ← 001-mvp-synced-grocery-list for Sebastiaan to review

---

## Dependencies & Execution Order

- **Phase 1** → **Phase 2** → **Phase 3 (US1 = MVP)** → **Phase 4 (US2)** → **Phase 5 (US3)** → **Phase 6 (US4)** → **Phase 7**
- US3 and US4 are server-light (T032 is the only server work) — frontend tasks T029–T031 and T033–T034 can start as soon as Phase 4's UI foundation exists
- Within Phase 2: T005→T006/T007 auth chain is serial; T008, T009, T010, T011, T014 parallel
- Server tests (T015/T016) before server impl (T017–T019); trip tests (T023) before trips impl (T024–T025)

## Parallel Opportunities

- **Two workstreams after Phase 2**: Workstream A = server (T015–T019, T023–T025, T032); Workstream B = frontend (T020–T022, T026–T031, T033–T034). Disjoint directories, shared contract = contracts/api.md
- All [P] tasks as marked
- T035–T038 independent of code work once contract is stable

## Implementation Strategy

- **MVP first**: Phases 1–3 → US1 demoable in two browsers on localhost; validate before proceeding
- **No commits from parallel workers**: Workstreams A and B leave changes in the working tree (disjoint dirs); the integrating session commits per checkpoint
- Each checkpoint: run quickstart steps, then commit

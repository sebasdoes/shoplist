# Phase 0 Research: Synced offline grocery list (MVP)

**Feature**: `001-mvp-synced-grocery-list` | **Date**: 2026-11-21

## D1 — Authentication: in-app OIDC public client (verified)

**Decision**: The PWA is an OIDC public client of Pocket ID (PKCE, S256). The sync server validates bearer JWTs against Pocket ID's JWKS. No client secret exists anywhere.

**Rationale — verified live against `https://id.akiha.nl/.well-known/openid-configuration`**:
- `issuer: https://id.akiha.nl`; `jwks_uri: https://id.akiha.nl/.well-known/jwks.json`
- `grant_types_supported` includes `authorization_code` and `refresh_token` (silent re-auth possible)
- `code_challenge_methods_supported` includes `S256`
- `offline_access` scope offered; request it to get refresh tokens
- Pocket ID supports public clients (API field `isPublic`); public clients require PKCE (pocket-id issue #757) — we always use PKCE, so compliant
- Client provisioning: created by the agent via the Pocket ID API (X-API-Key, `POST /api/oidc/clients`) with callbacks `https://shoplist.v.akiha.nl` and `http://localhost:5173` — not a manual admin step.

**Alternatives rejected**:
- *oauth2-proxy sidecar* — that pattern is for services that cannot speak OIDC (pocketid-oidc-proxy skill); we control the app, and a sidecar adds a cookie session layer the PWA sync flow doesn't need.
- *Confidential client* — requires a secret on the PWA side, which is impossible to keep in a public repo.

## D2 — Sync model: revision watermark + outbox, per-field LWW

**Decision**: Every server row carries a monotonic `rev` (global sequence). Clients pull with `{watermark}`; push is a batched outbox. Mutations are resolved per-field last-write-wins using server-receive timestamps. No merge UI.

**Rationale**: Two-user household produces trivial conflict density. CRDTs (Automerge/Yjs) buy nothing here and violate the boring-stack principle. Full event replay is unneeded — new devices pull a snapshot + history directly.

**Alternatives rejected**: CRDT document sync; WebSocket realtime (add later behind the same sync service if push latency annoys anyone; SSE is the cheap version).

## D3 — Client offline storage: IndexedDB + service worker precache

**Decision**: Svelte app stores items, outbox, token cache, and sync state in IndexedDB (via the small `idb` wrapper, ~1 kB). Service worker precaches app shell for full offline install.

**Alternatives rejected**: SQLite-WASM/OPFS (heavier toolchain for a two-table problem); localStorage (synchronous, quota-limited, no transactions).

## D4 — Suggestion ranking: decayed frequency

**Decision**: quick-add orders history by decayed frequency: `score = Σ_purchases 0.5^(days_since / 14)`, ties broken by most-recent purchase. X-deleted entries are hard-deleted and excluded permanently.

**Rationale**: one formula, indexable, produces sane "weekly stuff floats up, one-offs sink" behavior.

## D5 — Trip completion: one idempotent transaction

**Decision**: `Trip done` submits one client-generated trip id to a single endpoint. Server processes ticked→history, keep/drop decisions, and staple re-adds in ONE SQLite transaction; a repeated trip id is a no-op. Offline trips queue the whole trip in the outbox as a unit.

**Rationale**: eliminates every partial-trip race (both users pressing it, offline submit + retry).

## D6 — Name normalization for dedupe

**Decision**: `name_key` = casefold + trim + collapse internal whitespace + strip diacritics (NFKD). Uniqueness enforced on `(name_key, status='active')`; pushing a duplicate merges onto the existing row.

## D7 — JWT validation on server

**Decision**: PyJWT + cached JWKS (refresh on unknown `kid`, 1 h TTL), verify RS256, `iss=https://id.akiha.nl`, `aud=<client_id>`, `exp`. 401 on any failure; client treats 401 as "re-auth then replay outbox".

# Phase 1 API Contract: Shoplist sync server

**Feature**: `001-mvp-synced-grocery-list` | **Base**: `https://shoplist.v.akiha.nl/api/v1` (v.akiha.nl wildcard forwards without subdomain creation)

All requests: `Authorization: Bearer <JWT>`; 401 on invalid/expired → client re-auths via PKCE then replays outbox. All timestamps server-stamped on receive (LWW source). Bodies JSON; errors: `{"detail": "..."}` with 400/401/404/409/422.

## Endpoints

### POST /sync/push

Batch of pending client mutations, applied in order inside one transaction. Returns per-item server truth so the client reconciles.

```json
// request
{
  "mutations": [
    {"op": "upsert_item", "item": {"id": "uuid", "name": "koffie", "quantity": "2x",
      "note": "the small ones", "staple": true, "tombstone": false}},
    {"op": "upsert_item", "item": {"id": "uuid", "tombstone": true}},
    {"op": "trip", "trip": {"id": "uuid",
      "decisions": [{"item_id": "uuid", "outcome": "bought"} | {"outcome": "kept"} | {"outcome": "not_bought"}]}}
  ]
}
```

```json
// response
{
  "applied": [{"id": "uuid", "rev": 142, "updated_at": "...", "merged_into": null | "existing-uuid"}],
  "trip": {"id": "uuid", "duplicate": false, "readded": [{"id": "...", "name": "koffie"}]}
}
```

Semantics:
- `merged_into` non-null = dedupe hit: the pushed row folded into the existing active item (server returns the survivor; client adopts it).
- Trip application is atomic; duplicate trip id → `"duplicate": true`, no effects, client simply clears it from its outbox.
- Out-of-order pushes are safe: LWW by server-receive time makes ordering irrelevant except that a tombstone always wins over a concurrent field edit of the same revision epoch.

### POST /sync/pull

```json
// request  {"watermark": 140, "include_history": true}
// response
{
  "watermark": 142,
  "items": [ {"id": "...", "name": "...", "quantity": "...", "note": "...",
              "status": "active", "staple": true, "tombstone": false,
              "created_by": "...", "rev": 141} ],
  "history": [ {"id": "...", "name": "...", "quantity": "...", "note": "...",
                "outcome": "bought", "actor": "...", "occurred_at": "...", "rev": 142} ]
}
```

Semantics: returns rows with `rev > watermark` (cap: if the delta exceeds 500 rows it returns a full snapshot flag; a fresh device just calls with `watermark: 0`). `include_history: true` also returns the full (≤1000) history list — the suggestion source — and is the default for watermark 0 pulls.

### GET /history  → full history list (client refresh path; same payload as above's `history`).

### DELETE /history/{id} → 204. Hard delete (FR-011). No tombstone.

### GET /health → `{"ok": true}` unauthenticated, for Caddy/monitoring.

## Client sync loop (contract for the frontend)

1. Load IndexedDB snapshot → render instantly, network-independent.
2. If outbox non-empty and token valid → `POST /sync/push` → adopt `applied`, clear pushed entries, handle `merged_into`.
3. `POST /sync/pull` with stored watermark → upsert pulled rows → persist new watermark.
4. Re-run on: connectivity regain (`online` event), app focus, 60 s timer while visible, and after any local mutation (debounced 2 s).
5. 401 anywhere → silent PKCE re-auth → retry step 2. Failure → outbox retained, UI shows a quiet "syncing…" chip, never an error dialog.

## PWA surface

- `manifest.json` (name "Shoplist", standalone, theme/maskable icons, scope `/`), served with the app.
- Service worker: precache app shell; NO caching of API responses (IndexedDB is the cache); background-sync event flushes the outbox when supported.
- OIDC client registration (one-time, Sebastiaan): callback URL = app origin, PKCE public client, request scopes `openid profile email offline_access`.

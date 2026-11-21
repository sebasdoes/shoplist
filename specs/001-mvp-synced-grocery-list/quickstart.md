# Quickstart & Validation: Synced offline grocery list (MVP)

**Feature**: `001-mvp-synced-grocery-list`

## Prerequisites

- Python ≥ 3.12 + `uv`; Node ≥ 22
- A dev Pocket ID client (callback `http://localhost:5173`) — admin UI of sso.example.com
- Nothing else: SQLite is stdlib-adjacent, no external services

## Run locally

```bash
# server
cd server && uv sync && uv run uvicorn app.main:app --reload --port 8000
# tests (includes the sync/race/dedupe suite — these run BEFORE any UI work)
cd server && uv run pytest -q
# frontend
cd frontend && npm install && npm run dev          # http://localhost:5173
```

Set `OIDC_ISSUER=https://sso.example.com`, `OIDC_AUDIENCE=<client_id>` in `server/.env` (gitignored).

## Validate the stories end-to-end

**US1 — shared synced list** (two browser windows, normal + Incognito):

1. Both windows: sign in via Pocket ID (first run).
2. Window A adds "koffie" → appears in window B within ~2 s (local polling; phones use push-on-online).
3. DevTools → Network → "Offline" on window B; add "theeglas" → renders instantly, no error.
4. Reload window B (still offline) → item still there (IndexedDB).
5. Back to "Online" → item reaches the server (check window A / sqlite directly).
6. Add "melk" offline on both, bring both online → exactly one "melk" (dedupe, US1-4).

**US2 — trip done + staples**:

1. Tick two items ("koffie", "melk"), leave one unticked.
2. Press Trip done → ticked items vanish to history; unticked item asks keep/drop.
3. Mark "koffie" as staple beforehand with quantity "2x" → after trip done it re-appears active with "2x" prefilled.
4. Press Trip done again (with empty list / no changes) → no duplicates (check `/sync/pull` output).
5. Repeat the whole trip while offline → sync lands atomically on reconnect.

**US3 — per-field conflicts**: window A offline edits quantity of "melk"; window B edits its note; both online → both edits present.

**US4 — quick-add**: complete a trip, open quick-add drawer, recently-bought sorts top; tap to re-add with prefills; X a one-off entry → gone from suggestions permanently.

**SC checks**: offline add < 1 s (feels instant, no spinner); server RAM `systemd-cgtop`/`docker stats` < 50 MB.

## Deploy (delivered by the build phase, applied by User A)

GHCR image `ghcr.io/sebasdoes/shoplist-server`, compose service + Caddy block appended on the VPS; secrets scanned from tree + history before push. See constitution "Additional Constraints".

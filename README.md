# Shoplist

One shared grocery list for two people, that actually works offline.

User A + User B's replacement for shopping-list apps that were deprecated, over-complicated, or abandoned. A self-hosted PWA: fully usable with zero connectivity, quietly syncing through a tiny self-hosted server the moment a phone regains signal.

- **Stack**: FastAPI + SQLite (server, `server/`) · Svelte 5 + Vite PWA (client, `frontend/`) · served from ONE container
- **Offline-first**: every mutation persists to IndexedDB before touching the network; the outbox replays on reconnect — nothing typed in airplane mode is ever lost
- **Conflicts**: per-field last-write-wins on server-received timestamps, auto-dedupe of identical names. No merge screens, no CRDT
- **Trip done**: one idempotent server event — bought items go to history, unticked items get a one-tap keep/drop, staple items re-add themselves with their last quantity/note
- **Auth**: Pocket ID only (OIDC public client, PKCE) — this app stores no passwords and manages no users
- **Deployment**: single image at `ghcr.io/sebasdoes/shoplist-server`, SQLite volume, behind Caddy at `https://shoplist.example.com`

## Repo layout

```text
server/       FastAPI sync server + pytest suite
frontend/     Svelte PWA + vitest suite
deploy/       compose service + Caddy snippet + OIDC client notes (applied by User A)
specs/        spec-kit docs: spec, plan, research, data model, API contract, tasks
```

## Development

See `specs/001-mvp-synced-grocery-list/quickstart.md` for the full guide, in brief:

```bash
cd server && uv sync && uv run pytest -q
uv run uvicorn app.main:app --reload --port 8000   # API on :8000

cd frontend && npm install && npm run dev           # PWA on :5173
npx vitest run && npm run build
```

OIDC dev values: issuer `https://sso.example.com`, client id in `deploy/OIDC-CLIENT.txt` (public PKCE client; the localhost callback is already registered).

## Governance

The contract lives in `specs/` — spec.md is the requirement, contracts/api.md the wire format, and `.specify/memory/constitution.md` the rules (including the permanently-rejected feature list: multiple lists, aisles, recipes, sharing outside the household, reminders). Change the spec before changing the code.

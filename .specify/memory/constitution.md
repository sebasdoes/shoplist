# Shoplist Constitution

## Core Principles

### I. No data loss, ever

Groceries typed on a phone in airplane mode are as safe as groceries typed online. Every user mutation is written to durable local storage before touching the network. Deletions are tombstones, not erasures. Conflict resolution never discards a user's edit silently without a documented rule.

### II. Offline is the default path, online is an optimization

The app must be fully usable with zero connectivity: browse, add, check off, delete, complete a trip. Sync is a background best-effort process that retries on reconnect. No feature may require a live connection to function, and the UI never blocks on network calls.

### III. Scope is frozen at one list

This is a replacement for shopping-list apps that died of complexity. Rejected permanently: multiple lists, aisle/category grouping, recipe integration, sharing with people outside the household, reminders, notification systems. The staple auto-re-add (US2) is in scope by explicit decision. Adding anything else requires amending this principle first, in writing.

### IV. Conflict resolution is boring by design

Per-item last-write-wins using server-received timestamps (client clocks are not trusted for ordering). Identical item names auto-dedupe at sync time. Trip-complete is a single server-side event, so two people cannot half-complete the same trip into inconsistent states. No CRDTs, no merge UI, no manual conflict cleanup screens.

### V. Zero user management

Authentication goes through Pocket ID (OIDC, public client, PKCE). This app never stores passwords, never creates accounts, never has roles or permissions. Two users: User A and User B. Access control entirely belongs to Pocket ID; the app only verifies identity tokens.

## Additional Constraints

- **Stack is fixed**: Python FastAPI + SQLite for the sync server; Svelte + Vite for the PWA. No new frameworks without an amendment.
- **Deployment**: container image on GHCR, run on the VPS behind the existing Caddy, applied by User A from a delivered file set + commands (agent does not touch the server directly). Validate with `caddy validate` / compose config before delivery.
- **Repo is public**: secrets are scanned out of the working tree AND git history before any push. Pocket ID client secrets are created by User A out-of-band; a public OIDC client design is preferred so no secret exists at all.
- **History retention**: purchase history kept forever; every history entry has a one-tap delete (X).

## Development Workflow

- TDD where it counts: sync merge logic, dedupe, and trip-complete transitions get explicit tests before implementation (these are the failure-prone parts). UI work is verified by manual device checks (two phones, airplane mode).
- Every spec requires acceptance scenarios per user story, written in Given/When/Then, before planning starts.
- Skills and docs must stay runnable by a cheap model: numbered steps, copy-paste commands, explicit gotchas.

## Governance

This constitution supersedes all other practices in this repo. Amendments require a documented reason, an entry in the version history below, and User A's approval. Complexity beyond a user story's acceptance scenarios must be justified against Principle III.

**Version**: 1.0.0 | **Ratified**: 2026-11-21 | **Last Amended**: 2026-11-21

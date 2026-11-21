# Feature Specification: Synced offline grocery list (MVP)

**Feature Branch**: `001-mvp-synced-grocery-list`

**Created**: 2026-11-21

**Status**: Draft

**Input**: User description: "Replacement for shopping-list apps that were deprecated, over-complex, or abandoned. One shared grocery list for Sebastiaan and Kim. Synced through a tiny self-hosted server on the VPS; fully usable offline, syncs automatically when connectivity returns."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Add items to a shared list that syncs (Priority: P1)

Sebastiaan opens Shoplist on his phone, signs in once through Pocket ID, and adds "koffie" to the list. Kim opens Shoplist on her phone and sees Sebastiaan's item within a few seconds. Either of them can be offline: items typed on a plane-train-subway-connection dead zone are stored locally and appear on the other person's phone the next time the device has connectivity. The app is added to both home screens and behaves like an app, not a bookmark.

**Why this priority**: This is the entire reason the project exists. Everything else is decoration on top of "two people, one truthful list, survives offline use."

**Independent Test**: Can be tested by signing in on two devices, adding items on both (one in airplane mode), restoring connectivity, and observing a merged list. Delivers the core daily value even if no other story ships.

**Acceptance Scenarios**:

1. **Given** both devices online, **When** Sebastiaan adds an item, **Then** Kim's list shows it within 10 seconds without a manual refresh.
2. **Given** Sebastiaan's phone in airplane mode, **When** he adds an item, **Then** the item appears in his list immediately (no spinner, no error) and is still there after closing and reopening the PWA.
3. **Given** airplane mode turned off again, **When** the phone reconnects, **Then** the queued item syncs automatically and Kim's phone shows it within 10 seconds.
4. **Given** both users simultaneously added "melk" while offline, **When** both devices sync, **Then** the merged list contains exactly one "melk".
5. **Given** a fresh install, **When** the user first opens the app, **Then** sign-in goes through Pocket ID once and the chosen credentials persist across PWA relaunches.

---

### User Story 2 - Finish shopping and restock staples (Priority: P1)

Standing in the store, Sebastiaan ticks items off as they go into the cart. Back at the register (or at home), he presses "Trip done". The confirmed items move to purchase history and disappear from the list. Any unticked items are presented one at a time for a single-tap decision: keep on the list or drop to history as "not bought". Finally, the app re-adds every item marked as a staple that is not currently on the list, pre-filled with the quantity and note from its last purchase, so Kim's shower gel and the coffee beans are never forgotten again.

**Why this priority**: This defines the weekly rhythm of the app. Without trip completion and staple restock, the list fills with cruft forever and staples get forgotten — the exact failure that made the old apps unusable.

**Independent Test**: Can be tested by completing a list with a mix of ticked, unticked ("keep" and "drop" decisions), and staple items, then verifying list contents and history. Delivers the full weekly workflow without the quick-add convenience layer.

**Acceptance Scenarios**:

1. **Given** three items ticked off, **When** "Trip done" is pressed, **Then** those three disappear from the list and appear in history with timestamp, quantity, note, and buyer.
2. **Given** two unticked items, **When** "Trip done" is pressed, **Then** each is offered for a one-tap keep/drop decision before the list is finalized.
3. **Given** a staple "koffie" checked off with quantity "2x", **When** "Trip done" is pressed, **Then** koffie re-enters the active list with quantity "2x" and its previous note, marked as a staple.
4. **Given** a staple already re-added, **When** Kim also finishes the trip from her phone, **Then** the second "Trip done" does not duplicate the staple or create a second trip event.
5. **Given** the trip done button pressed while offline, **When** connectivity returns, **Then** the trip event, history entries, and staple re-adds all land on the server in one consistent batch.

---

### User Story 3 - Quantity and note per item (Priority: P2)

Adding or editing an item supports an optional quantity ("2x", "500g") and a short note ("the small ones"). On the list these render inline with the item name. The staple toggle lives in the item edit sheet and sticks to the item across trips.

**Why this priority**: Real shopping lists carry qualifiers. Without them Kim buys the wrong coffee. Not P1 because a name-only list is still usable for a first release.

**Independent Test**: Can be tested by adding an item with quantity + note, verifying display, editing, checking off, and confirming both persist through a sync round trip (including offline edit while the other user edits the same item's name).

**Acceptance Scenarios**:

1. **Given** an item "koffie" with quantity "2x" and note "the small ones", **When** Kim views the synced list, **Then** both render with the item.
2. **Given** Sebastiaan edits the quantity while offline and Kim edits the note while offline, **When** both sync, **Then** both edits survive (last-write-wins is applied per field, not per item row).
3. **Given** an item marked as staple, **When** it is checked off and the trip completes, **Then** the staple flag stays with the item forever until someone removes it.

---

### User Story 4 - Quick-add from purchase history (Priority: P3)

When Kim needs something again that is not a staple, she opens the quick-add drawer, sees recent purchases sorted by recency/frequency, and taps one to put it back on the list with its last quantity and note. Every history entry has an X to delete it permanently (e.g. the item was a one-time experiment). History is kept forever.

**Why this priority**: Pure convenience; the app is fully usable without it. X-deletion exists because "forever" without pruning collects embarrassing ghosts (the 2024 attempt at making sauerkraut).

**Independent Test**: Can be tested by completing a trip with several items, then re-adding one from the quick-add drawer and deleting one via X.

**Acceptance Scenarios**:

1. **Given** five completed trips' worth of history, **When** the quick-add drawer opens, **Then** entries are ordered by combined recency and frequency, each rendered with name, quantity, and note.
2. **Given** a history entry, **When** its X is tapped and confirmed (single tap, undo snackbar instead of a confirm dialog), **Then** it never appears in suggestions again.
3. **Given** an item re-added from history, **When** the trip completes and that item was a staple, **Then** its history entry updated rather than duplicated.

---

### Edge Cases

- **Clock skew**: client device clocks are never trusted for conflict resolution; the server stamps all timestamps on receipt.
- **Offline create + delete**: an item added and deleted while offline propagates as a tombstone; the net result is it never appears on the server.
- **Token expiry while offline**: the PWA keeps working entirely from local storage; on reconnect, an expired session re-authenticates silently or prompts once — it never blocks local usage.
- **Empty or absurd input**: empty names rejected locally; names trimmed; hard cap on name/quantity/note length enforced identically client and server side.
- **Staple deleted from the list manually**: does not clear the staple flag; flag removal happens only in the edit sheet, so deletion does not silently disable restocking.
- **Both users complete the trip at once**: trip-complete is one idempotent server-side event; the second submission is a no-op.
- **First sync of a brand-new device**: device pulls the current list + recent history snapshot; no replay of ancient events needed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System provides exactly ONE shared grocery list accessible to authenticated household members. No list management UI exists.
- **FR-002**: System MUST allow adding items with a name, plus optional quantity and short note.
- **FR-003**: System MUST persist every mutation to on-device durable storage (IndexedDB) before any network interaction.
- **FR-004**: System MUST sync pending local mutations automatically when connectivity returns, without a manual refresh action.
- **FR-005**: System MUST authenticate via Pocket ID OIDC using a public client with PKCE; no passwords are stored by this system.
- **FR-006**: Sync server MUST verify an OIDC identity token against Pocket ID's JWKS on every API request and reject requests without a valid token.
- **FR-007**: System MUST resolve concurrent edits with per-field last-write-wins, ordered by server-received timestamps.
- **FR-008**: System MUST auto-dedupe items whose normalized names are identical at sync time (merge, keep both users' stamps).
- **FR-009**: System MUST implement trip completion as a single idempotent server-side event that atomically: (a) archives ticked items to history, (b) applies one-tap keep/drop decisions for unticked items, (c) re-adds staple items not currently active, with quantity/note from their last purchase.
- **FR-010**: System MUST let the user toggle the staple flag per item in the edit sheet.
- **FR-011**: System MUST expose purchase history forever, with entries ordered by recency/frequency in the quick-add view, and MUST let each entry be permanently deleted.
- **FR-012**: Deletion of list items MUST use tombstones so deletions propagate across offline devices.
- **FR-013**: System MUST be installable as a PWA on Android home screens and MUST work with no connectivity after first load (service worker + precached assets).
- **FR-014**: Any sync failure MUST be recoverable without data loss: failed mutations stay queued and are retried; the UI never silently drops an offline edit.

### Key Entities *(include if feature involves data)*

- **Item**: name (unique by normalization), quantity (optional), note (optional), status (active / completed / not-bought / dropped), staple flag, created_by, created_at, updated_at (server-received), tombstone flag.
- **Trip**: single completion event with id, completed_by, completed_at, and the resulting per-item decisions; idempotency key per trip.
- **History entry**: immutable record of a bought/not-bought outcome — item name, quantity, note, outcome, who, when; deletable permanently by any household member.
- **User**: identity only, resolved from the Pocket ID token (subject id + display name). Nothing else is stored.
- **Device sync state**: per-device watermark/revision used to pull incremental changes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Adding an item while fully offline completes in under 1 second with no error state, and the add survives an app restart.
- **SC-002**: An item added by one user appears on the other user's device within 10 seconds of both devices being online.
- **SC-003**: Two devices making independent offline edits to the same item lose zero edits (per-field LWW) and end with no duplicate-name items.
- **SC-004**: Airplane-mode round trip (add, check off, trip done, reopen app twice) functions with zero network access.
- **SC-005**: Sync server idles under 50 MB RAM and adds no manual maintenance burden — no cron jobs beyond its own event loop, no log rotation babysitting.

## Assumptions

- Sebastiaan and Kim both have (or will have) Pocket ID accounts; Kim can access the VPS over the public internet without VPN, which is precisely why auth is required.
- Both clients are Android browsers (Chrome/derivatives) adding the PWA to the home screen; desktop browser use is a nice-to-have, not a target.
- Pocket ID supports public (secret-less) OIDC clients with PKCE — must be verified during planning; fallback is a confidential client with the secret supplied to the server, not the PWA, or the oauth2-proxy sidecar pattern.
- Deployment follows the established VPS pattern: GHCR image, compose service behind Caddy, Sebastiaan applies delivered file sets himself.
- One list shared by the household means one data space on the server; multi-household/multi-list tenancy is explicitly rejected (Constitution III).

# Feature Specification: Multiple lists

**Feature Branch**: `002-multiple-lists`

**Created**: 2026-11-21

**Status**: Draft

**Input**: User description: "Multiple lists to separate our different stores and use cases. Shopping is the primary reason, but she might want a list for shop A or B, or a list for what we are going to eat this week. All are lists like we already have. Preferably in a hamburger menu."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Switch between lists via the menu (Priority: P1)

Existing data migrates into one default list named "Boodschappen" — nobody loses anything and the app looks familiar. A hamburger button opens a drawer listing every list; tapping one switches the whole view (items, add bar, trip button) to that list. The selection is remembered per device. Each list has its own Trip done button: completing the Boodschappen trip archives ONLY grocery items and re-adds ONLY grocery staples; ticked items on other lists are untouched.

**Why this priority**: This is the whole stakeholder ask. Without per-list independence the feature is unusable; without migration it's a data loss event.

**Independent Test**: Can be tested by creating two lists, adding distinct items to each, completing a trip on one, and verifying the other list is untouched. Delivers full daily value alone.

**Acceptance Scenarios**:

1. **Given** a deployment with existing items, **When** the schema migrates, **Then** all items and their history live in the list "Boodschappen" and nothing is lost.
2. **Given** two lists with items, **When** User A completes a trip on list 1, **Then** ticked items on list 2 remain active and list 2's staples do not re-add.
3. **Given** the drawer open, **When** a list is tapped, **Then** the list renders with its own items and the selection survives an app restart.
4. **Given** offline, **When** switching lists, **Then** each list's items come from IndexedDB — no network required.

---

### User Story 2 - Create and delete lists (Priority: P2)

The drawer has a "+ New list" action (name input, trimmed, non-empty, ≤100 chars) and a delete affordance per list. Deleting a list tombstones it AND all its items; those items leave every view (active or by-list) but their history rows stay for the global suggestion pool. Deleting the LAST remaining list is refused with a clear message — a household always keeps at least one. A deleted list's name may be reused.

**Why this priority**: Stakeholder explicitly wants per-store lists that appear and go; without create/delete the menu is a museum.

**Independent Test**: Can be tested by creating a list, adding items, deleting it, and verifying items vanish from all views while the remaining list still works.

**Acceptance Scenarios**:

1. **Given** the drawer, **When** "+ New list" with "Winkelpad A" is submitted, **Then** the list exists, is opened, and syncs to the other device within seconds.
2. **Given** a list with items, **When** deleted, **Then** it disappears from both devices and its items' tombstones propagate like any deletion.
3. **Given** only one list remains, **When** a delete is attempted, **Then** it is refused with a visible reason.
4. **Given** a deleted list's name, **When** a new list is created with the same name, **Then** it succeeds (names are not unique).

---

### User Story 3 - Global quick-add suggestions across lists (Priority: P2)

The quick-add history pool is household-global: suggestions on ANY list are ranked by decayed frequency over ALL purchase history. A suggestion's name no longer active on the CURRENT list is offerable; names already active on the current list are suppressed (unchanged behavior).

**Why this priority**: "Coffee" bought from Boodschappen should be addable from the store-A list without the history being duplicated per list.

**Independent Test**: complete a trip on list 1, open quick-add on list 2, and re-add the bought item there.

**Acceptance Scenarios**:

1. **Given** history from several lists, **When** quick-add opens on any list, **Then** suggestions rank by the global decayed-frequency score.
2. **Given** a suggestion name already active on the current list, **When** the drawer opens, **Then** that suggestion is suppressed (same rule as v1).
3. **Given** re-adding from another list, **Then** the new item is created ON that list with the history's quantity/note.

---

### Edge Cases

- Deleting a list while another device has it open: that device shows an "unavailable" state and switches to the first remaining list.
- Adding an item while a list is being deleted: tombstone wins (list is gone, item is gone), same precedence as item tombstones.
- List names are trimmed, ≤100 chars, and NOT unique — "Shop A" twice is allowed.
- Offline create/delete of lists: rides the same outbox; server applies in order like any mutation.
- Empty list: rendering an empty list shows the existing empty-hint.
- Migration races: the migration runs once at startup, guarded by the schema version; a second device hitting the old DB version pulls the migrated rows normally (single DB on the server, so there is exactly one migration).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-015**: System MUST support multiple named lists per household, presented in a hamburger drawer with list names, a "+ New list" action, and a per-list delete action.
- **FR-016**: Pre-existing data MUST migrate into one default list named "Boodschappen" exactly once, without data loss.
- **FR-017**: Trips, staples, ticked-item archiving, and keep/drop decisions MUST operate per list and never cross lists.
- **FR-018**: Deleting a list MUST tombstone the list and all of its items; its history rows MUST remain in the global suggestion pool.
- **FR-019**: The LAST remaining list MUST NOT be deletable.
- **FR-020**: The active list selection MUST persist per device (IndexedDB) and default to the first remaining list when the selection vanishes.
- **FR-021**: Quick-add suggestions MUST draw from the global household history pool, suppressed for names active on the current list.
- **FR-022**: List create/delete MUST sync offline-first through the same outbox mechanism as items (mutation ops in the same push batch).

### Key Entities *(include if feature involves data)*

- **List**: id (uuid), name (trimmed, ≤100, NOT unique), created_by, created_at, tombstone, rev. Exactly one "default" list seeded by migration ("Boodschappen").
- **Item**: gains `list_id` (required, references a live list at write time; list tombstone makes item tombstones).
- **Trip**: gains `list_id` — one idempotent completion event per list.
- **History entry**: gains `list_id` (informational; the suggestion pool is global across lists).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-006**: Zero data loss in the v1→v2 migration on the deployed DB (item count before == item count after, all visible in "Boodschappen").
- **SC-007**: Switching lists never fetches from the network (IndexedDB-only render), completes in <100 ms perceived.
- **SC-008**: A trip done on one list leaves every other list's active items and staples byte-identical.

## Assumptions

- List count stays small (tens); drawers render them all unordered, newest-created last.
- Deleting a list is confirm-protected by the standard undo-snackbar pattern (single tap, 5 s undo) — consistent with history deletion.
- Hamburger drawer slides from the left; the basket icon doubles as the menu button in the header.

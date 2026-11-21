// T007 — active list selection (FR-020). Lives outside stores.ts so both the
// store layer and the sync loop can use it without an import cycle.
// The selection persists in state.active_list and never triggers a network
// call: switching lists is IndexedDB-only (SC-007).
import { get, writable } from 'svelte/store';
import * as db from './db';

/**
 * The current list id. Initialised to the migration-seeded default uuid so a
 * fresh device (empty `lists` store, pre-pull) renders and adds against the
 * list the server seeds once sync starts.
 */
export const activeListId = writable<string>(db.DEFAULT_LIST_ID);

const ACTIVE_LIST_KEY = 'active_list';

function firstLive(lists: db.List[]): db.List | undefined {
  return [...lists]
    .filter((l) => !l.tombstone)
    .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id))[0];
}

/**
 * Re-resolve the active selection from the persisted value and the current
 * `lists` store: keep the stored selection when it is still live, otherwise
 * fall back to the first live list (FR-020) and persist that fallback.
 * With no lists at all (fresh device) the current/store default is kept.
 */
export async function reconcileActiveList(): Promise<void> {
  const lists = await db.getAllLists();
  const stored = await db.getState<string>(ACTIVE_LIST_KEY);
  const live = firstLive(lists);

  let next: string | null = null;
  if (stored !== null && lists.some((l) => l.id === stored && !l.tombstone)) {
    next = stored;
  } else if (live) {
    next = live.id;
  }

  if (next !== null && next !== stored) {
    await db.setState(ACTIVE_LIST_KEY, next);
  }
  if (next !== null) activeListId.set(next);
}

/** Switch the active list (drawer tap). Pure IndexedDB — no network, no sync. */
export async function setActiveList(id: string): Promise<void> {
  if (get(activeListId) === id) return;
  const list = await db.getList(id);
  if (!list || list.tombstone) return; // never select a tombstoned/missing list
  activeListId.set(id);
  await db.setState(ACTIVE_LIST_KEY, id);
}

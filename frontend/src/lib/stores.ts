// T021/T022/T026–T028/T030–T031/T034 — Svelte stores fed from IndexedDB.
// Constitution I/II: every mutation persists to IndexedDB BEFORE any network
// interaction; the UI reads only from what is in IndexedDB.
import { writable, derived, get } from 'svelte/store';
import * as db from './db';
import type { HistoryEntry, Item, OutboxItemMutation, TripDecision } from './db';
import { nameKey, validateItem, uuid } from './validate';
import {
  requestBackgroundSync,
  runSync,
  schedulePostMutationSync,
  onSyncComplete,
  onSyncStatus,
  type SyncStatusValue,
} from './sync';
import type { Suggestion } from './rank';
import {
  flushHistoryDeletes,
  getPendingDeletes,
  removeHistoryLocally,
  restoreHistory,
} from './history';

// ---- core stores ----

export const itemsStore = writable<Item[]>([]);
export const historyStore = writable<HistoryEntry[]>([]);
export const syncStatus = writable<SyncStatusValue>('idle');
export const userStore = writable<{ sub: string; name: string } | null>(null); // T022

/** The single list: active, not tombstoned. Unticked queue first (newest on
 * top for thumb reach), ticked visually separate below. */
export const activeItems = derived(itemsStore, (items) => {
  const live = items.filter((i) => i.status === 'active' && !i.tombstone);
  const fresh = (a: Item, b: Item) =>
    (b.local_created_at || '').localeCompare(a.local_created_at || '');
  const unticked = live.filter((i) => !i.ticked).sort(fresh);
  const ticked = live.filter((i) => i.ticked).sort(fresh);
  return [...unticked, ...ticked];
});

const byOccurredDesc = (a: HistoryEntry, b: HistoryEntry) =>
  b.occurred_at.localeCompare(a.occurred_at);

export const historySorted = derived(historyStore, (h) => [...h].sort(byOccurredDesc));

/** X-deleted entries stay hidden until the server confirms (or undo — T033). */
const pendingDeletes = writable<Set<string>>(new Set());

export const historyVisible = derived([historySorted, pendingDeletes], ([rows, pending]) =>
  rows.filter((r) => !pending.has(r.id)),
);

// ---- loading (render path is IndexedDB only) ----

export async function loadStoresFromDb(): Promise<void> {
  itemsStore.set(await db.getAllItems());
  historyStore.set(await db.getAllHistory());
}

/** Keep stores in step after each silent sync completes. */
export function wireStoresToSync(): void {
  onSyncComplete(() => {
    void loadStoresFromDb();
  });
}

export function resetStoresForTests(): void {
  itemsStore.set([]);
  historyStore.set([]);
  syncStatus.set('idle');
  userStore.set(null);
  pendingDeletes.set(new Set());
}

// ---- item helpers ----

function itemToPush(item: Item): OutboxItemMutation {
  return {
    id: item.id,
    name: item.name,
    quantity: item.quantity,
    note: item.note,
    staple: item.staple,
    tombstone: item.tombstone,
  };
}

async function reloadItems(): Promise<void> {
  itemsStore.set(await db.getAllItems());
}

async function reloadHistory(): Promise<void> {
  historyStore.set(await db.getAllHistory());
}

// ---- US1: add / delete ----

/** Add bar entry point (T021). Throws a validation message for the caller. */
export async function addItem(
  input: { name: string; quantity?: string | null; note?: string | null; staple?: boolean },
  opts: { createdBy?: string } = {},
): Promise<Item> {
  const name = input.name.trim();
  const quantity = input.quantity?.trim() || null;
  const note = input.note?.trim() || null;
  const err = validateItem({ name, quantity, note });
  if (err) throw new Error(err);

  const now = new Date().toISOString();
  const item: Item = {
    id: uuid(),
    name,
    name_key: nameKey(name),
    quantity,
    note,
    status: 'active',
    staple: input.staple ?? false,
    tombstone: false,
    created_by: opts.createdBy || 'local',
    created_at: now, // placeholder; the server stamps on receipt
    updated_at: now,
    rev: 0,
    ticked: false,
    ticked_at: null,
    local_created_at: now,
  };

  // 1. persist BEFORE network (constitution I), 2. queue mutation.
  await db.putItem(item);
  await db.enqueueOutbox({
    op: 'upsert_item',
    item: itemToPush(item),
    fields: ['name', 'quantity', 'note', 'staple'],
    ts: now,
  });
  await reloadItems();
  void requestBackgroundSync();
  schedulePostMutationSync();
  return item;
}

/** Manual delete → tombstone mutation (FR-012). Staple flag survives. */
export async function deleteItem(id: string): Promise<void> {
  const item = await db.getItem(id);
  if (!item || item.tombstone) return;
  const tombstoned: Item = { ...item, tombstone: true, updated_at: new Date().toISOString() };
  await db.putItem(tombstoned);
  await db.enqueueOutbox({
    op: 'upsert_item',
    item: { id: tombstoned.id, tombstone: true },
    fields: ['tombstone'],
    ts: new Date().toISOString(),
  });
  await reloadItems();
  void requestBackgroundSync();
  schedulePostMutationSync();
}

// ---- US2: check-off + trip ----

/** T027 — tick persists to IndexedDB immediately. The tick itself has no
 * server analogue in contracts/api.md; the ticked set feeds `Trip done`,
 * which is what the server receives as one mutation. */
export async function setTick(item: Item, ticked: boolean): Promise<void> {
  const now = new Date().toISOString();
  await db.putItem({ ...item, ticked, ticked_at: ticked ? now : null });
  await reloadItems();
}

export async function toggleTick(item: Item): Promise<void> {
  await setTick(item, !item.ticked);
}

/** T026 — whole trip queued as ONE outbox unit (idempotent server event). */
export async function completeTrip(decisions: TripDecision[]): Promise<string> {
  if (decisions.length === 0) return '';

  for (const d of decisions) {
    const item = await db.getItem(d.item_id);
    if (!item) continue;
    if (d.outcome === 'bought') {
      await db.putItem({ ...item, status: 'completed', ticked: false, ticked_at: null });
    } else if (d.outcome === 'not_bought') {
      await db.putItem({ ...item, status: 'dropped', ticked: false, ticked_at: null });
    }
    // kept → still active, unchanged
  }

  const now = new Date().toISOString();
  const tripId = uuid();
  await db.enqueueOutbox({ op: 'trip', trip: { id: tripId, decisions }, ts: now });
  await reloadItems();
  void requestBackgroundSync();
  schedulePostMutationSync();
  return tripId;
}

// ---- US3: edit sheet ----

/** T029 — validated edit; queues a per-field mutation (T031 merge contract). */
export async function saveItemEdit(
  item: Item,
  draft: { name: string; quantity: string | null; note: string | null; staple: boolean },
): Promise<void> {
  const name = draft.name.trim();
  const quantity = draft.quantity?.trim() || null;
  const note = draft.note?.trim() || null;
  const err = validateItem({ name, quantity, note });
  if (err) throw new Error(err);

  const changed: string[] = [];
  if (name !== item.name) changed.push('name');
  if ((quantity ?? null) !== (item.quantity ?? null)) changed.push('quantity');
  if ((note ?? null) !== (item.note ?? null)) changed.push('note');
  if (draft.staple !== item.staple) changed.push('staple');
  if (changed.length === 0) return;

  const updated: Item = {
    ...item,
    name,
    name_key: nameKey(name),
    quantity,
    note,
    staple: draft.staple,
    updated_at: new Date().toISOString(),
  };
  await db.putItem(updated);

  const payload: OutboxItemMutation = { id: updated.id };
  if (changed.includes('name')) payload.name = name;
  if (changed.includes('quantity')) payload.quantity = quantity;
  if (changed.includes('note')) payload.note = note;
  if (changed.includes('staple')) payload.staple = draft.staple;

  await db.enqueueOutbox({
    op: 'upsert_item',
    item: payload,
    fields: changed,
    ts: updated.updated_at,
  });
  await reloadItems();
  void requestBackgroundSync();
  schedulePostMutationSync();
}

// ---- US4: quick-add re-add (T034 reuses the T021 add pipeline) ----

export function reAddFromHistory(suggestion: Suggestion, opts: { createdBy?: string } = {}) {
  return addItem(
    {
      name: suggestion.name,
      quantity: suggestion.quantity,
      note: suggestion.note,
      staple: suggestion.staple,
    },
    opts,
  );
}

// ---- US4: X delete + undo snackbar (T033) ----

const undoTimers = new Map<string, ReturnType<typeof setTimeout>>();
const lastRemoved = new Map<string, HistoryEntry>();
const UNDO_MS = 5000;

async function refreshPendingDeletes(): Promise<void> {
  const fromDb = await getPendingDeletes();
  pendingDeletes.set(new Set<string>([...fromDb, ...lastRemoved.keys()]));
}

/** X tap: optimistic remove now; server DELETE fires after the 5 s undo window. */
export async function deleteHistoryEntry(id: string): Promise<HistoryEntry | null> {
  const entry = get(historyStore).find((h) => h.id === id) ?? (await db.getHistoryEntry(id));
  if (!entry) return null;

  lastRemoved.set(id, entry);
  await removeHistoryLocally(entry);
  await Promise.all([reloadHistory(), refreshPendingDeletes()]);

  const timer = setTimeout(() => {
    undoTimers.delete(id);
    void commitHistoryDelete(id);
  }, UNDO_MS);
  undoTimers.set(id, timer);
  return entry;
}

async function commitHistoryDelete(id: string): Promise<void> {
  try {
    await flushHistoryDeletes();
  } catch {
    // Quiet failure: still pending, retried on the next sync run (FR-014).
  }
  lastRemoved.delete(id);
  await refreshPendingDeletes();
}

/** Undo within the 5 s window: restore the entry, cancel the DELETE. */
export async function undoHistoryDelete(id: string): Promise<void> {
  const timer = undoTimers.get(id);
  if (timer) {
    clearTimeout(timer);
    undoTimers.delete(id);
  }
  const entry = lastRemoved.get(id) ?? (await db.getHistoryEntry(id));
  lastRemoved.delete(id);
  if (!entry) {
    await refreshPendingDeletes();
    return;
  }
  await restoreHistory(entry);
  await Promise.all([reloadHistory(), refreshPendingDeletes()]);
}

/** Test hook: resolve all pending undo windows immediately. */
export async function flushUndoTimersForTests(): Promise<void> {
  for (const [, t] of undoTimers) clearTimeout(t);
  undoTimers.clear();
  pendingDeletes.set(new Set());
  lastRemoved.clear();
  try {
    await flushHistoryDeletes();
  } catch {
    /* quiet */
  }
}

// ---- sync wiring ----

// Module-level: the syncStatus store reflects sync.ts status without needing
// a mounted component (tests assert against it directly).
onSyncStatus((s) => syncStatus.set(s));

export async function runSyncNow(reason = 'manual'): Promise<SyncStatusValue> {
  await runSync(reason);
  return get(syncStatus);
}

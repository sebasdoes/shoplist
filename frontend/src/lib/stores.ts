// T021/T022/T026–T028/T030–T031/T034 — Svelte stores fed from IndexedDB.
// Constitution I/II: every mutation persists to IndexedDB BEFORE any network
// interaction; the UI reads only from what is in IndexedDB.
import { writable, derived, get } from 'svelte/store';
import * as db from './db';
import type { HistoryEntry, Item, List, OutboxItemMutation, TripDecision } from './db';
import { nameKey, validateItem, uuid } from './validate';
import { bumpQuantityText } from './quantity';
import {
  requestBackgroundSync,
  runSync,
  schedulePostMutationSync,
  onSyncComplete,
  onSyncStatus,
  type SyncStatusValue,
} from './sync';
import type { Suggestion } from './rank';
import { activeListId, setActiveList, reconcileActiveList } from './active-list';
import {
  flushHistoryDeletes,
  getPendingDeletes,
  removeHistoryLocally,
  restoreHistory,
} from './history';

// ---- core stores ----

export const itemsStore = writable<Item[]>([]);
export const historyStore = writable<HistoryEntry[]>([]);
/** T007 — every non-tombstoned list, fresh from IndexedDB. */
export const listsStore = writable<List[]>([]);
export const syncStatus = writable<SyncStatusValue>('idle');
export const userStore = writable<{ sub: string; name: string } | null>(null); // T022

// Re-exported so components only ever import from stores.
export { activeListId, setActiveList };

/** The active List object (or null on a fresh device pre-pull). */
export const activeList = derived(
  [listsStore, activeListId],
  ([lists, id]) => lists.find((l) => l.id === id && !l.tombstone) ?? null,
);

/** The single list: active, not tombstoned. Unticked queue first, ticked
 * visually separate below. T007: scoped to the ACTIVE list only.
 * feature-004: the unticked queue orders by sort_order (schema v4, lower
 * renders first) with the legacy newest-first order as tie-break; ticked
 * rows keep the newest-first order (their sort_order is unused while ticked). */
export const activeItems = derived([itemsStore, activeListId], ([items, listId]) => {
  const live = items.filter(
    (i) => i.status === 'active' && !i.tombstone && (i.list_id ?? db.DEFAULT_LIST_ID) === listId,
  );
  const fresh = (a: Item, b: Item) =>
    (b.local_created_at || '').localeCompare(a.local_created_at || '');
  const byQueueOrder = (a: Item, b: Item) =>
    (a.sort_order ?? 0) - (b.sort_order ?? 0) || fresh(a, b) || a.id.localeCompare(b.id);
  const unticked = live.filter((i) => !i.ticked).sort(byQueueOrder);
  const ticked = live.filter((i) => i.ticked).sort(fresh);
  return [...unticked, ...ticked];
});

const byOccurredDesc = (a: HistoryEntry, b: HistoryEntry) =>
  b.occurred_at.localeCompare(a.occurred_at);

export const historySorted = derived(historyStore, (h) => [...h].sort(byOccurredDesc));

/** feature-003 — canonical drawer order: (sort_order asc, created_at asc).
 * Pre-v3 rows (sort_order missing on disk) read as 0, so they keep their
 * created_at order. Ties on both fall back to id for a stable sequence. */
export function sortListsForDisplay(lists: List[]): List[] {
  return [...lists].sort(
    (a, b) =>
      (a.sort_order ?? 0) - (b.sort_order ?? 0) ||
      a.created_at.localeCompare(b.created_at) ||
      a.id.localeCompare(b.id),
  );
}

/** X-deleted entries stay hidden until the server confirms (or undo — T033). */
const pendingDeletes = writable<Set<string>>(new Set());

export const historyVisible = derived([historySorted, pendingDeletes], ([rows, pending]) =>
  rows.filter((r) => !pending.has(r.id)),
);

// ---- loading (render path is IndexedDB only) ----

export async function loadStoresFromDb(): Promise<void> {
  itemsStore.set(await db.getAllItems());
  historyStore.set(await db.getAllHistory());
  listsStore.set(await db.getAllLists());
  await reconcileActiveList(); // FR-020 fallback resolution
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
  listsStore.set([]);
  activeListId.set(db.DEFAULT_LIST_ID);
  syncStatus.set('idle');
  userStore.set(null);
  pendingDeletes.set(new Set());
}

// ---- item helpers ----

function itemToPush(item: Item): OutboxItemMutation {
  return {
    id: item.id,
    list_id: item.list_id,
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

/** feature-004 — top-insert rank for a NEW active row of a list: one below
 * the lowest active sort_order of that list (server applies the same rule, so
 * the client optimistic order matches the post-sync truth). */
export async function nextTopSortOrder(listId: string): Promise<number> {
  const actives = (await db.getAllItems()).filter(
    (i) => !i.tombstone && i.status === 'active' && (i.list_id ?? db.DEFAULT_LIST_ID) === listId,
  );
  if (actives.length === 0) return 0;
  const min = actives.reduce((m, i) => Math.min(m, i.sort_order ?? 0), Infinity);
  return min - 1;
}

async function reloadHistory(): Promise<void> {
  historyStore.set(await db.getAllHistory());
}

async function reloadLists(): Promise<void> {
  listsStore.set(await db.getAllLists());
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

  const listId = get(activeListId) || db.DEFAULT_LIST_ID;
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
    list_id: listId,
    // feature-004: new ACTIVE items enter at the TOP of the unticked queue.
    sort_order: await nextTopSortOrder(listId),
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
    item: { id: tombstoned.id, list_id: tombstoned.list_id, tombstone: true },
    fields: ['tombstone'],
    ts: new Date().toISOString(),
  });
  await reloadItems();
  void requestBackgroundSync();
  schedulePostMutationSync();
}

// ---- US-lists: create / delete lists (FR-015/018/019, T007) ----

/** validateList mirrors data-model.md: trimmed, 1–100 chars, NOT unique. */
function validateListName(raw: string): { name: string; err: string | null } {
  const name = raw.trim();
  if (!name) return { name, err: 'Name is required' };
  if (name.length > 100) return { name, err: 'Name must be at most 100 characters' };
  return { name, err: null };
}

/** '+ New list' (T009): persists locally FIRST, then queues create_list. */
export async function createList(input: string, opts: { createdBy?: string } = {}): Promise<List> {
  const { name, err } = validateListName(input);
  if (err) throw new Error(err);

  const now = new Date().toISOString();
  // feature-003: seed the new list at the END of the current order (max+1),
  // mirroring the v3 server's create_list rule.
  const allLists = await db.getAllLists();
  const nextSortOrder = allLists.reduce((m, l) => Math.max(m, l.sort_order ?? 0), -1) + 1;
  const list: List = {
    id: uuid(),
    name,
    created_by: opts.createdBy || 'local',
    created_at: now,
    tombstone: false,
    rev: 0,
    sort_order: nextSortOrder,
  };

  // 1. persist BEFORE network (constitution I), 2. queue the mutation,
  // 3. open the new list (US2-1: "the list exists, is opened").
  await db.putList(list);
  await db.enqueueOutbox({
    op: 'create_list',
    list: { id: list.id, name: list.name },
    ts: now,
  });
  await reloadLists();
  await setActiveList(list.id);
  void requestBackgroundSync();
  schedulePostMutationSync();
  return list;
}

interface RemovedListSnapshot {
  list: List;
  items: Item[];
}

const listUndoTimers = new Map<string, ReturnType<typeof setTimeout>>();
const removedLists = new Map<string, RemovedListSnapshot>();
const LIST_UNDO_MS = 5000;

/** Per-list delete (single tap + 5 s undo). Tombstones the list AND all of
 * its items locally right away (FR-018); the delete_list outbox mutation is
 * only queued once the undo window expires. Refuses to delete the LAST live
 * list (FR-019). */
export async function deleteList(id: string): Promise<void> {
  const list = await db.getList(id);
  if (!list || list.tombstone) return; // already deleted — no-op
  const liveLists = (await db.getAllLists()).filter((l) => !l.tombstone);
  if (liveLists.length <= 1) throw new Error('cannot delete the last list');

  // cascade: tombstone the list + every live item of that list
  const items = (await db.getAllItems()).filter((i) => i.list_id === id && !i.tombstone);
  const now = new Date().toISOString();
  await db.putList({ ...list, tombstone: true });
  for (const item of items) {
    await db.putItem({ ...item, tombstone: true, updated_at: now });
  }

  removedLists.set(id, { list, items });
  await reloadLists();
  await reloadItems();
  // fall back off the deleted list if it was active (FR-020)
  if (get(activeListId) === id) await reconcileActiveList();

  const timer = setTimeout(() => {
    listUndoTimers.delete(id);
    void commitListDelete(id);
  }, LIST_UNDO_MS);
  listUndoTimers.set(id, timer);
}

async function commitListDelete(id: string): Promise<void> {
  const snapshot = removedLists.get(id);
  if (!snapshot) return;
  removedLists.delete(id);
  // queue the mutation — it rides the outbox like any other (FR-022)
  await db.enqueueOutbox({ op: 'delete_list', list_id: id, ts: new Date().toISOString() });
  void requestBackgroundSync();
  schedulePostMutationSync();
}

/** Undo within the 5 s window: restore the list and its items, cancel the delete. */
export async function undoDeleteList(id: string): Promise<void> {
  const timer = listUndoTimers.get(id);
  if (timer) {
    clearTimeout(timer);
    listUndoTimers.delete(id);
  }
  const snapshot = removedLists.get(id);
  removedLists.delete(id);
  if (!snapshot) return;
  await db.putList({ ...snapshot.list, tombstone: false });
  for (const item of snapshot.items) {
    await db.putItem({ ...item, tombstone: false });
  }
  await Promise.all([reloadLists(), reloadItems()]);
  // no reconcile: the undone list is live again; keep whatever selection the
  // delete fell back to unless it no longer exists
  await reconcileActiveList();
}

// ---- feature-003: household-wide list reordering (up/down arrows) ----

/** Optimistic reorder: rebuild the full ordered id array client-side, apply
 * the new sort_order to IndexedDB IMMEDIATELY (offline-first, constitution I),
 * then enqueue ONE `reorder_lists` mutation that propagates household-wide.
 * Ids the caller omitted (or unknown/tombstoned ids) keep their current
 * display order and are appended so the op always covers every live list. */
export async function reorderLists(orderedIds: string[]): Promise<void> {
  const live = (await db.getAllLists()).filter((l) => !l.tombstone);
  const liveIds = new Set(live.map((l) => l.id));

  const seen = new Set<string>();
  const order: string[] = [];
  for (const id of orderedIds) {
    if (liveIds.has(id) && !seen.has(id)) {
      order.push(id);
      seen.add(id);
    }
  }
  for (const l of sortListsForDisplay(live)) {
    if (!seen.has(l.id)) order.push(l.id);
  }
  if (order.length <= 1) return;

  const rank = new Map(order.map((id, i) => [id, i] as const));
  const rows: List[] = live.map((l) => ({ ...l, sort_order: rank.get(l.id) as number }));
  // 1. persist BEFORE any network (constitution I), 2. queue the op.
  await db.putLists(rows);
  await db.enqueueOutbox({ op: 'reorder_lists', order, ts: new Date().toISOString() });
  await reloadLists();
  void requestBackgroundSync();
  schedulePostMutationSync();
}

// ---- feature-004: drag-reorder the unticked shopping queue ----

/** Canonical unticked queue order (mirrors the activeItems derived sort). */
function untickedQueueOrder(live: Item[]): Item[] {
  const fresh = (a: Item, b: Item) =>
    (b.local_created_at || '').localeCompare(a.local_created_at || '');
  return live
    .filter((i) => !i.ticked)
    .sort(
      (a, b) =>
        (a.sort_order ?? 0) - (b.sort_order ?? 0) || fresh(a, b) || a.id.localeCompare(b.id),
    );
}

/** Optimistic drag-end commit (called once per drop, after the live preview):
 * apply the new sort_order to IndexedDB IMMEDIATELY (constitution I), then
 * enqueue ONE `reorder_items` mutation with the complete unticked active set
 * of that list. Unknown ids are dropped, omitted ids keep their current slot,
 * and an unchanged sequence writes nothing — no mutation, no sync trigger. */
export async function reorderItems(listId: string, orderedIds: string[]): Promise<void> {
  const live = (await db.getAllItems()).filter(
    (i) => !i.tombstone && i.status === 'active' && (i.list_id ?? db.DEFAULT_LIST_ID) === listId,
  );
  const queue = untickedQueueOrder(live);
  const current = queue.map((i) => i.id);
  const queueIds = new Set(current);

  const order: string[] = [];
  for (const id of orderedIds) {
    if (queueIds.has(id) && !order.includes(id)) order.push(id);
  }
  // complete the set — the server's final authority expects the full active set
  for (const id of current) {
    if (!order.includes(id)) order.push(id);
  }
  if (order.length <= 1 || order.join('\u0000') === current.join('\u0000')) return;

  const rank = new Map(order.map((id, i) => [id, i] as const));
  const rows = queue.map((i) => ({ ...i, sort_order: rank.get(i.id) as number }));
  // 1. persist BEFORE any network (constitution I), 2. queue ONE op.
  await db.putItems(rows);
  await db.enqueueOutbox({ op: 'reorder_items', list_id: listId, order, ts: new Date().toISOString() });
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
  // FR-017: the trip is scoped to the list of its own items (decisions never
  // cross lists — the server rejects and rolls back otherwise).
  const firstItem = await db.getItem(decisions[0].item_id);
  const listId = firstItem?.list_id || get(activeListId) || db.DEFAULT_LIST_ID;
  await db.enqueueOutbox({ op: 'trip', trip: { id: tripId, list_id: listId, decisions }, ts: now });
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

  const payload: OutboxItemMutation = { id: updated.id, list_id: updated.list_id };
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

// ---- quantity stepper: +/− on the row bumps the first integer, clamped at 1 ----
export async function bumpQuantity(item: Item, delta: number): Promise<void> {
  const next = bumpQuantityText(item.quantity, delta);
  if (next === null || next === item.quantity) return;
  await saveItemEdit(item, {
    name: item.name,
    quantity: next,
    note: item.note,
    staple: item.staple,
  });
}

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
  // list undo windows: queue their delete_list mutations now
  for (const id of [...listUndoTimers.keys()]) {
    clearTimeout(listUndoTimers.get(id));
    listUndoTimers.delete(id);
    if (removedLists.has(id)) removedLists.delete(id);
    await db.enqueueOutbox({ op: 'delete_list', list_id: id, ts: new Date().toISOString() });
  }
  // only for lists that still exist as tombstoned (deletes that were flushed
  // above); an undone list must NOT be deleted
  const tombstoned = new Set((await db.getAllLists()).filter((l) => l.tombstone).map((l) => l.id));
  const outbox = await db.getOutbox();
  const stale = outbox
    .filter((e) => e.op === 'delete_list' && !tombstoned.has(e.list_id))
    .map((e) => e.seq);
  await db.deleteOutboxSeqs(stale);
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

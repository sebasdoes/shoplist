import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { rankSuggestions } from '../src/lib/rank';
import * as db from '../src/lib/db';
import type { HistoryEntry, List } from '../src/lib/db';
import {
  activeListId,
  addItem,
  activeItems,
  createList,
  completeTrip,
  deleteList,
  flushUndoTimersForTests,
  historySorted,
  loadStoresFromDb,
  runSyncNow,
  setActiveList,
  setTick,
  undoDeleteList,
} from '../src/lib/stores';
import { configureTestApi, activeNames } from './helpers';

beforeEach(configureTestApi);

function seedLiveList(overrides: Partial<List> = {}): List {
  const now = new Date().toISOString();
  return {
    id: overrides.id ?? crypto.randomUUID(),
    name: overrides.name ?? 'Boodschappen',
    created_by: overrides.created_by ?? 'server',
    created_at: overrides.created_at ?? now,
    tombstone: overrides.tombstone ?? false,
    rev: overrides.rev ?? 10,
    sort_order: overrides.sort_order ?? 0, // feature-003 (schema v3)
  };
}

function historyEntry(overrides: Partial<HistoryEntry> = {}): HistoryEntry {
  return {
    id: overrides.id ?? crypto.randomUUID(),
    item_id: null,
    name: overrides.name ?? 'koffie',
    quantity: overrides.quantity ?? null,
    note: overrides.note ?? null,
    outcome: overrides.outcome ?? 'bought',
    staple_snapshot: overrides.staple_snapshot ?? false,
    actor: 'user-a',
    occurred_at: overrides.occurred_at ?? new Date().toISOString(),
    rev: overrides.rev ?? 1,
  } as HistoryEntry;
}

describe('lists (T006–T011)', () => {
  // T008 / SC-007 / US1-4 — switching lists renders that list's items from
  // IndexedDB ONLY; offline switching must not touch the network.
  it('switching lists renders per-list items from IndexedDB with zero network', async () => {
    const fetchSpy = vi.fn(async () => {
      throw new TypeError('offline');
    });
    vi.stubGlobal('fetch', fetchSpy);

    const a = await createList('Store A');
    const b = await createList('Store B'); // create switches active to B
    await addItem({ name: 'koffie' }); // lands on B
    await setActiveList(a.id);
    await addItem({ name: 'brood' }); // lands on A

    await loadStoresFromDb();
    expect(activeNames()).toEqual(['brood']); // A only

    await setActiveList(b.id);
    await loadStoresFromDb();
    expect(activeNames()).toEqual(['koffie']); // B only — switch is pure IndexedDB

    expect(fetchSpy).not.toHaveBeenCalled(); // SC-007: no network on switching
    expect(await db.getState('active_list')).toBe(b.id); // FR-020 persistence
  });

  // FR-020 — a vanished (tombstoned) selection falls back to the first live list.
  it('falls back to the first live list when the persisted selection is tombstoned', async () => {
    const first = await createList('Boodschappen');
    const second = await createList('Store B');
    await setActiveList(second.id);
    expect(await db.getState('active_list')).toBe(second.id);

    // Server tombstones Store B; the pull snapshot carries the tombstoned list.
    vi.stubGlobal(
      'fetch',
      routeListsPull({
        watermark: 90,
        lists: [
          { id: first.id, name: 'Boodschappen', rev: 88, tombstone: false },
          { id: second.id, name: 'Store B', rev: 90, tombstone: true },
        ],
        items: [],
        history: [],
      }),
    );
    expect(await runSyncNow('test')).toBe('idle');
    expect(get(activeItems)).toHaveLength(0); // nothing lost, nothing leaked

    await loadStoresFromDb();
    expect(get(activeListId)).toBe(first.id);
    expect(await db.getState('active_list')).toBe(first.id); // fallback persisted
  });

  // T008 / SC-008 / FR-017 — a trip on list A leaves list B byte-identical.
  it('trip on list A does not touch list B; the trip mutation carries list_id', async () => {
    const a = await createList('Store A');
    await addItem({ name: 'brood' }); // A
    await addItem({ name: 'thee' }); // A
    const b = await createList('Store B'); // switching active to B
    await addItem({ name: 'koffie' }); // B

    await loadStoresFromDb();
    await setActiveList(a.id); // view list A
    const list = get(activeItems);
    const brood = list.find((i) => i.name === 'brood')!;
    const thee = list.find((i) => i.name === 'thee')!;
    await setTick(brood, true);
    await setTick(thee, true);
    await completeTrip([
      { item_id: brood.id, outcome: 'bought' },
      { item_id: thee.id, outcome: 'bought' },
    ]);

    // local transitions apply to A only
    expect((await db.getItem(brood.id))?.status).toBe('completed');
    expect((await db.getItem(thee.id))?.status).toBe('completed');
    const koffie = (await db.getAllItems()).find((i) => i.name === 'koffie')!;
    expect(koffie.status).toBe('active');
    expect(koffie.list_id).toBe(b.id);
    expect(koffie.tombstone).toBe(false);

    // queue carries ONE trip mutation scoped to list A
    const outbox = await db.getOutbox();
    const trips = outbox.filter((e) => e.op === 'trip');
    expect(trips).toHaveLength(1);
    expect((trips[0] as { trip: { list_id: string } }).trip.list_id).toBe(a.id);

    await loadStoresFromDb();
    await setActiveList(a.id);
    expect(activeNames()).toEqual([]); // A emptied by the trip
    await setActiveList(b.id);
    expect(activeNames()).toEqual(['koffie']); // B untouched
  });

  // FR-018 — deleting a list tombstones it AND all of its items, with undo.
  it('delete cascades tombstones to its items; undo restores; other lists untouched', async () => {
    const doomed = await createList('Doomed');
    await addItem({ name: 'zeep' }); // on Doomed
    const survivor = await createList('Survivor');
    await addItem({ name: 'koffie' }); // on Survivor

    await loadStoresFromDb();
    await deleteList(doomed.id);
    await loadStoresFromDb();

    // cascade: list + its items tombstoned
    expect((await db.getList(doomed.id))?.tombstone).toBe(true);
    const zeep = (await db.getAllItems()).find((i) => i.name === 'zeep')!;
    expect(zeep.tombstone).toBe(true);
    // survivor untouched
    expect((await db.getList(survivor.id))?.tombstone).toBe(false);
    const koffie = (await db.getAllItems()).find((i) => i.name === 'koffie')!;
    expect(koffie.tombstone).toBe(false);
    expect(get(activeListId)).toBe(survivor.id); // active fell back off the deleted list

    // undo restores both the list and its items before the window closes
    await undoDeleteList(doomed.id);
    expect((await db.getList(doomed.id))?.tombstone).toBe(false);
    expect((await db.getItem(zeep.id))?.tombstone).toBe(false);
    // undo again without a pending delete is a no-op
    await undoDeleteList(doomed.id);
    expect((await db.getList(doomed.id))?.tombstone).toBe(false);

    // an undone delete NEVER rides the outbox
    await flushUndoTimersForTests();
    expect((await db.getOutbox()).filter((e) => e.op === 'delete_list')).toHaveLength(0);

    // once the window expires (no undo), the delete_list mutation rides the outbox (T011)
    await deleteList(doomed.id);
    await flushUndoTimersForTests();
    const outbox = await db.getOutbox();
    const deletes = outbox.filter((e) => e.op === 'delete_list');
    expect(deletes).toHaveLength(1);
    expect((deletes[0] as { list_id: string }).list_id).toBe(doomed.id);
  });

  it('a completed delete persists BEFORE any network touch (constitution I)', async () => {
    const fetchSpy = vi.fn(async () => {
      throw new TypeError('offline');
    });
    vi.stubGlobal('fetch', fetchSpy);
    const a = await createList('A');
    await createList('B');
    await addItem({ name: 'koffie' });
    setActiveList(a.id);
    await deleteList(a.id);
    await flushUndoTimersForTests();

    expect((await db.getList(a.id))?.tombstone).toBe(true);
    const deletes = (await db.getOutbox()).filter((e) => e.op === 'delete_list');
    expect(deletes).toHaveLength(1);
    expect(fetchSpy).not.toHaveBeenCalled(); // queue-then-network, not the reverse
  });

  // FR-019 — the LAST remaining list cannot be deleted.
  it('refuses to delete the last list with a clear message', async () => {
    const only = await createList('Boodschappen');
    await addItem({ name: 'koffie' });

    await expect(deleteList(only.id)).rejects.toThrow(/cannot delete the last list/i);
    expect((await db.getList(only.id))?.tombstone).toBe(false);
    expect((await db.getAllItems()).every((i) => !i.tombstone)).toBe(true);
    expect((await db.getOutbox()).filter((e) => e.op === 'delete_list')).toHaveLength(0);
  });

  // T011 / FR-022 — create_list rides the normal outbox, offline-first.
  it('queues create_list offline, pushes the contract shape, applies applied.lists', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('offline');
      }),
    );
    const list = await createList('Winkelpad A');
    expect((await db.getList(list.id))?.name).toBe('Winkelpad A');
    await flushUndoTimersForTests();
    const queued = (await db.getOutbox()).filter((e) => e.op === 'create_list');
    expect(queued).toHaveLength(1);
    expect((queued[0] as { list: { id: string; name: string } }).list).toEqual({
      id: list.id,
      name: 'Winkelpad A',
    });

    // back online: the outbox drains with the contract push shape
    let pushBody: unknown;
    vi.stubGlobal(
      'fetch',
      routeListsPush({
        push: (body) => {
          pushBody = body;
          return {
            applied: {
              items: [],
              lists: [{ id: list.id, name: 'Winkelpad A', rev: 70, tombstone: false }],
            },
          };
        },
        pull: () => ({ watermark: 70, items: [], history: [] }),
      }),
    );
    expect(await runSyncNow('test')).toBe('idle');
    const muts = (pushBody as { mutations: { op: string; list?: { id: string } }[] }).mutations;
    expect(muts).toHaveLength(1);
    expect(muts[0].op).toBe('create_list');
    expect(muts[0].list?.id).toBe(list.id);
    expect(await db.getOutbox()).toHaveLength(0);
    expect((await db.getList(list.id))?.rev).toBe(70); // server rev adopted
    expect(await db.getState('active_list')).toBe(list.id);
  });

  // T011 — watermark-0 snapshot includes ALL lists (tombstoned too) and items
  // carry list_id; unknown/tombstoned list ids never leak into the view.
  it('pull applies the lists snapshot including tombstoned rows and list-scoped items', async () => {
    const live = seedLiveList({ id: 'list-live', name: 'Boodschappen' });
    const gone = seedLiveList({ id: 'list-gone', name: 'Old list', rev: 40 });
    vi.stubGlobal(
      'fetch',
      routeListsPull({
        watermark: 0,
        lists: [
          { id: live.id, name: live.name, rev: live.rev, tombstone: false },
          { id: gone.id, name: gone.name, rev: gone.rev, tombstone: true },
        ],
        items: [
          {
            id: 'i-1',
            name: 'koffie',
            quantity: null,
            note: null,
            status: 'active',
            staple: false,
            tombstone: false,
            created_by: 'user-a',
            rev: 30,
            list_id: 'list-live',
          },
        ],
      }),
    );
    expect(await runSyncNow('test')).toBe('idle');
    const all = await db.getAllLists();
    expect(all.map((l) => l.id).sort()).toEqual(['list-gone', 'list-live']); // tombstoned kept
    const row = await db.getItem('i-1');
    expect(row?.list_id).toBe('list-live');

    await loadStoresFromDb();
    expect(get(activeListId)).toBe('list-live'); // FR-020 default on a fresh device
    expect(activeNames()).toEqual(['koffie']);
  });

  // T010 / FR-021 — suggestions stay the GLOBAL history pool, suppressed only
  // for names active on the CURRENT list.
  it('quick-add suggests global history on any list, suppressing current-list names', async () => {
    const a = await createList('Store A');
    const b = await createList('Store B');
    await db.putHistory(historyEntry({ name: 'koffie', occurred_at: new Date().toISOString() }));
    await loadStoresFromDb();

    await setActiveList(a.id);
    let suggestions = rankSuggestions(
      get(historySorted),
      Date.now(),
      new Set(get(activeItems).map((i) => i.name_key)),
    );
    expect(suggestions.map((s) => s.name)).toEqual(['koffie']);

    // name active on the current list → suppressed (unchanged rule)
    await addItem({ name: 'koffie' }); // lands on A
    await loadStoresFromDb();
    suggestions = rankSuggestions(
      get(historySorted),
      Date.now(),
      new Set(get(activeItems).map((i) => i.name_key)),
    );
    expect(suggestions).toHaveLength(0);

    // same active name on A does NOT suppress it on B — the pool is global
    await setActiveList(b.id);
    await loadStoresFromDb();
    suggestions = rankSuggestions(
      get(historySorted),
      Date.now(),
      new Set(get(activeItems).map((i) => i.name_key)),
    );
    expect(suggestions.map((s) => s.name)).toEqual(['koffie']);
    void a;
  });

  // T006 — v1 IndexedDBs are migrated by bumping the version and clearing the
  // mirrored stores for a watermark-0 resync.
  it('migrates a v1 local DB: lists store exists, mirrored stores cleared for resync', async () => {
    await db.deleteDatabaseForTests();
    // Simulate a v1 install: old stores with old-shape rows, no `lists`.
    const raw = await openRawV1();
    await putV1Row(raw, 'items', {
      id: 'old-1',
      name: 'koffie',
      status: 'active',
      tombstone: false,
      rev: 5,
    });
    await putV1Row(raw, 'state', 12, 'watermark');
    raw.close();

    await db.initDb(); // triggers the v1→v2 upgrade
    expect(await db.getAllLists()).toEqual([]); // empty until the resync pull
    await db.putList(seedLiveList({ id: 'new-list' }));
    expect((await db.getAllLists()).map((l) => l.id)).toEqual(['new-list']);
    expect(await db.getItem('old-1')).toBeUndefined(); // cleared for full resync
    expect(await db.getState('watermark')).toBeNull(); // forces watermark-0 pull
  });
});

// ---- helpers ----

/** Route only the sync endpoints with plain JSON (list-aware). */
function routeListsPush(handlers: {
  push?: (body: unknown) => { applied?: unknown[]; lists?: unknown[] } | object;
  pull?: (body: unknown) => object;
}): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const body = JSON.parse(String(init?.body ?? '{}'));
    if (url.endsWith('/sync/push')) {
      if (!handlers.push) return new Response(JSON.stringify({ applied: [], lists: [] }));
      return new Response(JSON.stringify(handlers.push(body)));
    }
    if (url.endsWith('/sync/pull')) {
      if (!handlers.pull) {
        return new Response(JSON.stringify({ watermark: 100, items: [], history: [] }));
      }
      return new Response(JSON.stringify(handlers.pull(body)));
    }
    if (url.endsWith('/history')) return new Response(JSON.stringify([]));
    throw new TypeError(`unexpected fetch ${url}`);
  }) as typeof fetch;
}

function routeListsPull(res: {
  watermark: number;
  lists?: unknown[];
  items?: unknown[];
  history?: unknown[];
}): typeof fetch {
  return routeListsPush({ pull: () => res });
}

function openRawV1(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('shoplist', 1);
    req.onupgradeneeded = () => {
      const d = req.result;
      const items = d.createObjectStore('items', { keyPath: 'id' });
      items.createIndex('by_rev', 'rev');
      items.createIndex('by_status', 'status');
      const history = d.createObjectStore('history', { keyPath: 'id' });
      history.createIndex('by_occurred_at', 'occurred_at');
      history.createIndex('by_rev', 'rev');
      d.createObjectStore('outbox', { keyPath: 'seq', autoIncrement: true });
      d.createObjectStore('state');
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function putV1Row(db: IDBDatabase, store: string, value: unknown, key?: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, 'readwrite');
    (key ? tx.objectStore(store).put(value, key) : tx.objectStore(store).put(value)).onsuccess =
      () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

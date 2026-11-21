// T005 — feature-003: drawer account section, rows ordered by (sort_order,
// created_at), optimistic up/down reordering (IndexedDB-first), reorder_lists
// outbox op, and sort_order adoption on push-echo + pull.
// The vitest env is node, so the drawer is exercised with Svelte's server
// render; the reorder pipeline is exercised end-to-end against fake-indexeddb.
import { get } from 'svelte/store';
import { render } from 'svelte/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as db from '../src/lib/db';
import ListMenu from '../src/components/ListMenu.svelte';
import { configureTestApi, seedList } from './helpers';
import {
  createList,
  listsStore,
  loadStoresFromDb,
  reorderLists,
  runSyncNow,
  sortListsForDisplay,
  userStore,
} from '../src/lib/stores';

beforeEach(configureTestApi);

/** Minimal router for the sync endpoints (list-aware, v3 shapes). */
function routeMenu(handlers: {
  push?: (body: unknown) => object;
  pull?: (body: unknown) => object;
}): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/sync/push')) {
      if (!handlers.push) {
        return new Response(JSON.stringify({ applied: { items: [], lists: [] } }));
      }
      return new Response(JSON.stringify(handlers.push(JSON.parse(String(init?.body ?? '{}')))));
    }
    if (url.endsWith('/sync/pull')) {
      if (!handlers.pull) {
        return new Response(JSON.stringify({ watermark: 100, items: [], history: [] }));
      }
      return new Response(JSON.stringify(handlers.pull(JSON.parse(String(init?.body ?? '{}')))));
    }
    if (url.endsWith('/history')) return new Response(JSON.stringify([]));
    throw new TypeError(`unexpected fetch ${url}`);
  }) as typeof fetch;
}

describe('menu polish + reordering (feature-003)', () => {
  it('createList assigns sequential sort_order (local max+1, mirrors the v3 server)', async () => {
    const a = await createList('A');
    const b = await createList('B');
    const c = await createList('C');
    expect((await db.getList(a.id))?.sort_order).toBe(0);
    expect((await db.getList(b.id))?.sort_order).toBe(1);
    expect((await db.getList(c.id))?.sort_order).toBe(2);
  });

  it('sortListsForDisplay orders by (sort_order asc, created_at asc)', () => {
    const rows = [
      seedList({ id: '1', name: 'late-0', sort_order: 0, created_at: '2026-01-03T00:00:00Z' }),
      seedList({ id: '2', name: 'early-0', sort_order: 0, created_at: '2026-01-01T00:00:00Z' }),
      seedList({ id: '3', name: 'one', sort_order: 1, created_at: '2026-01-01T00:00:00Z' }),
    ];
    expect(sortListsForDisplay(rows).map((l) => l.name)).toEqual(['early-0', 'late-0', 'one']);
  });

  it('drawer renders the account section (user + Sign out) and rows in sort_order', async () => {
    const onsignout = vi.fn();
    await db.putList(seedList({ id: 'l-b', name: 'Beta', sort_order: 1 }));
    await db.putList(seedList({ id: 'l-a', name: 'Alpha', sort_order: 0 }));
    await db.putList(seedList({ id: 'l-c', name: 'Gamma', sort_order: 2 }));
    await loadStoresFromDb();
    userStore.set({ sub: 'u1', name: 'Ria' });

    const { html } = render(ListMenu, { props: { onclose: vi.fn(), onsignout, onhistory: vi.fn() } });

    expect(html).toContain('Sign out');
    expect(html).toContain('History');
    expect(html).toContain('Ria');
    // rows render in (sort_order asc) order, not insertion order
    expect(html.indexOf('Alpha')).toBeLessThan(html.indexOf('Beta'));
    expect(html.indexOf('Beta')).toBeLessThan(html.indexOf('Gamma'));
    expect(html).toContain('Move Alpha up');
  });

  it('reorderLists persists to IndexedDB immediately (offline-first) and enqueues reorder_lists', async () => {
    const fetchSpy = vi.fn(async () => {
      throw new TypeError('offline');
    });
    vi.stubGlobal('fetch', fetchSpy);

    const a = await createList('A');
    const b = await createList('B');
    const c = await createList('C');
    await reorderLists([c.id, a.id, b.id]);

    expect(fetchSpy).not.toHaveBeenCalled(); // constitution I: IndexedDB BEFORE network
    expect((await db.getList(c.id))?.sort_order).toBe(0);
    expect((await db.getList(a.id))?.sort_order).toBe(1);
    expect((await db.getList(b.id))?.sort_order).toBe(2);

    const entries = (await db.getOutbox()).filter((e) => e.op === 'reorder_lists');
    expect(entries).toHaveLength(1);
    expect((entries[0] as { order: string[] }).order).toEqual([c.id, a.id, b.id]);
  });

  it('reorderLists covers every live list even when passed a subset', async () => {
    await createList('A');
    const b = await createList('B');
    await createList('C');
    await reorderLists([b.id]);
    const order = sortListsForDisplay(await db.getAllLists()).map((l) => l.name);
    expect(order).toEqual(['B', 'A', 'C']);
  });

  it('flushes reorder_lists as a push mutation and adopts applied.lists sort_order', async () => {
    const a = await createList('A');
    const b = await createList('B');
    const c = await createList('C');
    await reorderLists([c.id, a.id, b.id]);

    let pushBody: unknown;
    vi.stubGlobal(
      'fetch',
      routeMenu({
        push: (body) => {
          pushBody = body;
          return {
            applied: {
              items: [],
              lists: [
                { id: c.id, name: 'C', rev: 91, tombstone: false, sort_order: 0 },
                { id: a.id, name: 'A', rev: 90, tombstone: false, sort_order: 1 },
                { id: b.id, name: 'B', rev: 92, tombstone: false, sort_order: 2 },
              ],
            },
          };
        },
        pull: () => ({ watermark: 91, lists: [], items: [], history: [] }),
      }),
    );

    expect(await runSyncNow('test')).toBe('idle');
    const muts = (pushBody as { mutations: { op: string; order?: string[] }[] }).mutations;
    const reorder = muts.find((m) => m.op === 'reorder_lists');
    expect(reorder?.order).toEqual([c.id, a.id, b.id]);

    expect(await db.getOutbox()).toHaveLength(0); // cleared once confirmed
    expect((await db.getList(a.id))?.rev).toBe(90); // server rev adopted
    expect((await db.getList(a.id))?.sort_order).toBe(1); // server order adopted
    expect((await db.getList(b.id))?.sort_order).toBe(2);

    await loadStoresFromDb();
    expect(sortListsForDisplay(get(listsStore)).map((l) => l.id)).toEqual([c.id, a.id, b.id]);
  });

  it('pull adopts sort_order from list rows (schema v3)', async () => {
    vi.stubGlobal(
      'fetch',
      routeMenu({
        pull: () => ({
          watermark: 5,
          lists: [
            { id: 'l-2', name: 'Second', rev: 3, tombstone: false, sort_order: 1 },
            { id: 'l-1', name: 'First', rev: 2, tombstone: false, sort_order: 0 },
          ],
          items: [],
          history: [],
        }),
      }),
    );

    expect(await runSyncNow('test')).toBe('idle');
    expect((await db.getList('l-1'))?.sort_order).toBe(0);
    expect((await db.getList('l-2'))?.sort_order).toBe(1);

    await loadStoresFromDb();
    expect(sortListsForDisplay(get(listsStore)).map((l) => l.name)).toEqual(['First', 'Second']);
  });
});

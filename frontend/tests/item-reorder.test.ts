// T005 — feature-004: drag-reorder of the unticked queue.
// vitest env is node (no DOM pointer events), so the pipeline that pointer
// release triggers is exercised here while the pointer math lives in
// src/lib/drag.ts as pure functions with unit coverage below:
//   drag end → IndexedDB FIRST → one reorder_items outbox op → scheduled sync
//   → server echo adopted (fresh revs + sort_order).
import { get } from 'svelte/store';
import { render } from 'svelte/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as db from '../src/lib/db';
import type { Item } from '../src/lib/db';
import ItemRow from '../src/components/ItemRow.svelte';
import {
  activeItems,
  addItem,
  loadStoresFromDb,
  reorderItems,
  runSyncNow,
  setActiveList,
} from '../src/lib/stores';
import { moveItemToIndex, sequenceChanged, slotIndexAt, type RowRect } from '../src/lib/drag';
import { configureTestApi, seedItem, seedList } from './helpers';
import { clearPostMutationTimerForTests } from '../src/lib/sync';

beforeEach(configureTestApi);

const LIST = 'l-four';

/** Server-shaped backfill: newest first, sequential 0..n-1 (matches v4). */
function seedQueue(
  entries: { id: string; name: string; sort_order: number; ticked?: boolean }[],
): Item[] {
  const base = entries.length; // descending local_created_at with sort_order
  return entries.map((e) =>
    seedItem({
      id: e.id,
      name: e.name,
      sort_order: e.sort_order,
      list_id: LIST,
      local_created_at: new Date(base - e.sort_order).toISOString(),
      ticked: e.ticked ?? false,
      ticked_at: e.ticked ? new Date().toISOString() : null,
    }),
  );
}

async function seedListAndItems(
  entries: { id: string; name: string; sort_order: number; ticked?: boolean }[],
): Promise<void> {
  await db.putList(seedList({ id: LIST }));
  await db.putItems(seedQueue(entries));
  await db.setState('active_list', LIST);
  await setActiveList(LIST);
}

describe('item reordering (feature-004)', () => {
  it('reorderItems persists to IndexedDB immediately and enqueues ONE reorder_items op, with zero network first', async () => {
    const fetchSpy = vi.fn(async () => {
      throw new TypeError('offline');
    });
    vi.stubGlobal('fetch', fetchSpy);

    // display order today: c, b, a (backfill newest-first)
    await seedListAndItems([
      { id: 'a', name: 'a', sort_order: 2 },
      { id: 'b', name: 'b', sort_order: 1 },
      { id: 'c', name: 'c', sort_order: 0 },
    ]);
    await loadStoresFromDb();
    expect(get(activeItems).map((i) => i.id)).toEqual(['c', 'b', 'a']);

    // drag c to the bottom → a, b, c
    await reorderItems(LIST, ['a', 'b', 'c']);

    expect(fetchSpy).not.toHaveBeenCalled(); // constitution I: IDB BEFORE network
    const rows = await db.getAllItems();
    expect(rows.find((i) => i.id === 'a')?.sort_order).toBe(0);
    expect(rows.find((i) => i.id === 'b')?.sort_order).toBe(1);
    expect(rows.find((i) => i.id === 'c')?.sort_order).toBe(2);

    const entries = (await db.getOutbox()).filter((e) => e.op === 'reorder_items');
    expect(entries).toHaveLength(1); // ONE op per drag, not per preview step
    expect(entries[0]).toMatchObject({ op: 'reorder_items', list_id: LIST, order: ['a', 'b', 'c'] });
  });

  it('reorderItems completes the active unticked set even when passed a subset', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('offline');
      }),
    );
    await seedListAndItems([
      { id: 'a', name: 'a', sort_order: 2 },
      { id: 'b', name: 'b', sort_order: 1 },
      { id: 'c', name: 'c', sort_order: 0 },
    ]);
    await loadStoresFromDb();

    await reorderItems(LIST, ['c', 'a']); // b omitted

    const outbox = await db.getOutbox();
    const entries = outbox.filter((e) => e.op === 'reorder_items');
    expect(entries).toHaveLength(1);
    // full active set, b appended in its current slot
    expect((entries[0] as { order: string[] }).order).toEqual(['c', 'a', 'b']);
    expect((await db.getItem('b'))?.sort_order).toBe(2);
  });

  it('reorderItems is a no-op (no outbox op) when the sequence is unchanged', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('offline');
      }),
    );
    await seedListAndItems([
      { id: 'a', name: 'a', sort_order: 2 },
      { id: 'b', name: 'b', sort_order: 1 },
      { id: 'c', name: 'c', sort_order: 0 },
    ]);
    await loadStoresFromDb();

    await reorderItems(LIST, ['c', 'b', 'a']); // same as current display

    expect(await db.getOutbox()).toHaveLength(0);
  });

  it('reorder leaves ticked rows alone: they stay grouped below and keep their sort_order', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('offline');
      }),
    );
    await seedListAndItems([
      { id: 'a', name: 'a', sort_order: 2 },
      { id: 'b', name: 'b', sort_order: 1 },
      { id: 'c', name: 'c', sort_order: 0 },
      { id: 'd', name: 'd', sort_order: 3, ticked: true },
    ]);
    await loadStoresFromDb();

    await reorderItems(LIST, ['a', 'b', 'c']);

    const outbox = await db.getOutbox();
    const entries = outbox.filter((e) => e.op === 'reorder_items');
    expect(entries).toHaveLength(1);
    expect((entries[0] as { order: string[] }).order).toEqual(['a', 'b', 'c']); // ticked 'd' not sent
    expect((await db.getItem('d'))?.sort_order).toBe(3); // untouched while ticked

    await loadStoresFromDb();
    expect(get(activeItems).map((i) => i.id)).toEqual(['a', 'b', 'c', 'd']); // unticked first, ticked below
  });

  it('order survives a reload (persisted in IndexedDB, not just store state)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('offline');
      }),
    );
    await seedListAndItems([
      { id: 'a', name: 'a', sort_order: 2 },
      { id: 'b', name: 'b', sort_order: 1 },
      { id: 'c', name: 'c', sort_order: 0 },
    ]);
    await loadStoresFromDb();
    await reorderItems(LIST, ['a', 'b', 'c']);

    await loadStoresFromDb(); // fresh read from IndexedDB
    expect(get(activeItems).map((i) => i.id)).toEqual(['a', 'b', 'c']);
  });

  it('new items enter at the TOP (sort_order = min active sort_order − 1)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('offline');
      }),
    );
    await seedListAndItems([
      { id: 'a', name: 'a', sort_order: 2 },
      { id: 'b', name: 'b', sort_order: 1 },
      { id: 'c', name: 'c', sort_order: 0 },
    ]);
    await loadStoresFromDb();

    const thee = await addItem({ name: 'thee' });

    expect((await db.getItem(thee.id))?.sort_order).toBe(-1); // below every active row
    await loadStoresFromDb();
    expect(get(activeItems).map((i) => i.name)).toEqual(['thee', 'c', 'b', 'a']);

    // a second add goes even lower (top again)
    const salt = await addItem({ name: 'salt' });
    expect((await db.getItem(salt.id))?.sort_order).toBe(-2);
    await loadStoresFromDb();
    expect(get(activeItems)[0].id).toBe(salt.id);
  });

  it('flushes reorder_items as the flat push mutation and adopts the applied.items echo (fresh revs + sort_order)', async () => {
    await seedListAndItems([
      { id: 'a', name: 'a', sort_order: 2 },
      { id: 'b', name: 'b', sort_order: 1 },
      { id: 'c', name: 'c', sort_order: 0 },
    ]);
    await loadStoresFromDb();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('offline');
      }),
    );
    await reorderItems(LIST, ['a', 'b', 'c']);

    const order = ['a', 'b', 'c'];
    let pushBody: unknown;
    vi.stubGlobal(
      'fetch',
      (async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const body = JSON.parse(String(init?.body ?? '{}'));
        if (url.endsWith('/sync/push')) {
          pushBody = body;
          return new Response(
            JSON.stringify({
              applied: {
                items: order.map((id, i) => ({
                  id,
                  rev: 50 + i,
                  updated_at: '2026-11-21T00:00:00Z',
                  merged_into: null,
                  sort_order: i,
                })),
                lists: [],
              },
            }),
          );
        }
        if (url.endsWith('/sync/pull')) {
          return new Response(JSON.stringify({ watermark: 51, items: [], history: [] }));
        }
        if (url.endsWith('/history')) return new Response(JSON.stringify([]));
        throw new TypeError(`unexpected fetch ${url}`);
      }) as typeof fetch,
    );

    expect(await runSyncNow('test')).toBe('idle');

    const muts = (pushBody as { mutations: { op: string; list_id?: string; order?: string[] }[] })
      .mutations;
    const reorder = muts.find((m) => m.op === 'reorder_items');
    expect(reorder).toEqual({ op: 'reorder_items', list_id: LIST, order }); // flat wire shape

    expect(await db.getOutbox()).toHaveLength(0); // cleared once confirmed
    // server echo adopted: fresh revs + server-assigned sort_order
    expect((await db.getItem('a'))?.sort_order).toBe(0);
    expect((await db.getItem('b'))?.sort_order).toBe(1);
    expect((await db.getItem('c'))?.sort_order).toBe(2);
    expect((await db.getItem('a'))?.rev).toBe(50);
    expect((await db.getItem('b'))?.rev).toBe(51);
    expect((await db.getItem('c'))?.rev).toBe(52);

    await loadStoresFromDb();
    expect(get(activeItems).map((i) => i.id)).toEqual(['a', 'b', 'c']);
  });

  it('pull adopts sort_order from item rows (schema v4)', async () => {
    await db.putList(seedList({ id: LIST }));
    await db.setState('active_list', LIST);
    await setActiveList(LIST);
    vi.stubGlobal(
      'fetch',
      (async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith('/sync/pull')) {
          return new Response(
            JSON.stringify({
              watermark: 7,
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
                  rev: 5,
                  list_id: LIST,
                  sort_order: 1,
                },
                {
                  id: 'i-2',
                  name: 'brood',
                  quantity: null,
                  note: null,
                  status: 'active',
                  staple: false,
                  tombstone: false,
                  created_by: 'user-a',
                  rev: 6,
                  list_id: LIST,
                  sort_order: 0,
                },
              ],
              history: [],
            }),
          );
        }
        if (url.endsWith('/sync/push')) {
          return new Response(JSON.stringify({ applied: { items: [], lists: [] } }));
        }
        if (url.endsWith('/history')) return new Response(JSON.stringify([]));
        throw new TypeError(`unexpected fetch ${url}`);
      }) as typeof fetch,
    );

    expect(await runSyncNow('test')).toBe('idle');
    expect((await db.getItem('i-2'))?.sort_order).toBe(0);
    expect((await db.getItem('i-1'))?.sort_order).toBe(1);

    await loadStoresFromDb();
    expect(get(activeItems).map((i) => i.id)).toEqual(['i-2', 'i-1']); // server order drives the queue
  });
});

describe('drag math (src/lib/drag.ts)', () => {
  const rects: RowRect[] = [
    { top: 0, bottom: 100 },
    { top: 100, bottom: 200 },
    { top: 200, bottom: 300 },
  ];

  it('slotIndexAt maps a pointer y to the insertion slot (gap semantics)', () => {
    expect(slotIndexAt(rects, 49)).toBe(0); // top half of row 1
    expect(slotIndexAt(rects, 51)).toBe(1); // bottom half → after row 1
    expect(slotIndexAt(rects, 149)).toBe(1); // top half of row 2 → before it
    expect(slotIndexAt(rects, 249)).toBe(2); // top half of row 3 → before it
    expect(slotIndexAt(rects, -5)).toBe(0); // above everything
    expect(slotIndexAt(rects, 999)).toBe(3); // below everything → after the last row
    expect(slotIndexAt([], 5)).toBe(0);
  });

  it('moveItemToIndex reorders ids with clamped indices', () => {
    expect(moveItemToIndex(['a', 'b', 'c'], 'a', 2)).toEqual(['b', 'c', 'a']);
    expect(moveItemToIndex(['a', 'b', 'c'], 'c', 0)).toEqual(['c', 'a', 'b']);
    expect(moveItemToIndex(['a', 'b', 'c'], 'b', 1)).toEqual(['a', 'b', 'c']); // no move
    expect(moveItemToIndex(['a', 'b'], 'a', 99)).toEqual(['b', 'a']); // clamped
    expect(moveItemToIndex(['a', 'b'], 'a', -3)).toEqual(['a', 'b']); // clamped
    expect(sequenceChanged(['a', 'b'], ['b', 'a'])).toBe(true);
    expect(sequenceChanged(['a', 'b'], ['a', 'b'])).toBe(false);
  });
});

// SSR render check (node env, matching menu.test.ts): the grip handle exists
// on unticked rows only, and sits LEFT of the tick checkbox.
describe('ItemRow drag handle (SSR)', () => {
  it('renders the drag handle LEFT of the checkbox for unticked rows only', () => {
    const item = seedItem({ id: 'i-1', name: 'koffie' });
    const { html } = render(ItemRow, {
      props: { item, ontoggle: vi.fn(), onedit: vi.fn(), onqty: vi.fn(), onhandledown: vi.fn() },
    });
    expect(html).toContain('drag-handle');
    expect(html.indexOf('drag-handle')).toBeLessThan(html.indexOf('type="checkbox"'));
  });

  it('ticked rows get no drag handle', () => {
    const item = seedItem({ id: 'i-2', name: 'brood', ticked: true });
    const { html } = render(ItemRow, {
      props: { item, ontoggle: vi.fn(), onedit: vi.fn(), onqty: vi.fn(), onhandledown: vi.fn() },
    });
    expect(html).not.toContain('drag-handle');
  });

  it('rows without an onhandledown callback render no handle', () => {
    const item = seedItem({ id: 'i-3', name: 'thee' });
    const { html } = render(ItemRow, {
      props: { item, ontoggle: vi.fn(), onedit: vi.fn(), onqty: vi.fn() },
    });
    expect(html).not.toContain('drag-handle');
  });
});

// keep the debounce timer from leaking into the next test
clearPostMutationTimerForTests();

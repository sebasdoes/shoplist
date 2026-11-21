import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as db from '../src/lib/db';
import type { HistoryEntry } from '../src/lib/db';
import {
  addItem,
  deleteItem,
  saveItemEdit,
  toggleTick,
  setTick,
  loadStoresFromDb,
  activeItems,
  deleteHistoryEntry,
  undoHistoryDelete,
  flushUndoTimersForTests,
  historyVisible,
} from '../src/lib/stores';
import { runSyncNow } from '../src/lib/stores';
import { routeFetch, configureTestApi } from './helpers';

beforeEach(configureTestApi);

function historyEntry(overrides: Partial<HistoryEntry> = {}): HistoryEntry {
  return {
    id: overrides.id ?? crypto.randomUUID(),
    item_id: null,
    name: overrides.name ?? 'koffie',
    quantity: overrides.quantity ?? null,
    note: overrides.note ?? null,
    outcome: overrides.outcome ?? 'bought',
    staple_snapshot: overrides.staple_snapshot ?? false,
    actor: 'seb',
    occurred_at: overrides.occurred_at ?? new Date().toISOString(),
    rev: overrides.rev ?? 1,
    ...overrides,
  } as HistoryEntry;
}

describe('stores (T021/T027/T029/T033)', () => {
  it('persists the add to IndexedDB BEFORE the network is touched (constitution I)', async () => {
    let networkTouched = false;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        networkTouched = true;
        throw new TypeError('offline');
      }),
    );

    const item = await addItem({ name: '  koffie  ', quantity: '2x', note: 'the small ones' });

    // persist-first even with the network down
    expect(networkTouched).toBe(false);
    const row = await db.getItem(item.id);
    expect(row?.name).toBe('koffie'); // trimmed
    expect(row?.name_key).toBe('koffie');
    expect(row?.status).toBe('active');

    // queued mutation exists with the full contract payload
    const outbox = await db.getOutbox();
    expect(outbox).toHaveLength(1);
    expect((outbox[0] as { item: { name?: string; quantity?: string } }).item.name).toBe('koffie');
    expect((outbox[0] as { item: { quantity?: string } }).item.quantity).toBe('2x');
  });

  it('rejects empty names and over-long fields per data-model constraints', async () => {
    await expect(addItem({ name: '   ' })).rejects.toThrow(/required/i);
    await expect(addItem({ name: 'x'.repeat(201) })).rejects.toThrow(/200/i);
    await expect(addItem({ name: 'ok', quantity: 'q'.repeat(51) })).rejects.toThrow(/50/i);
    await expect(addItem({ name: 'ok', note: 'n'.repeat(201) })).rejects.toThrow(/200/i);
  });

  it('persists a check-off tick instantly and separates ticked items in the queue', async () => {
    const a = await addItem({ name: 'koffie' });
    const b = await addItem({ name: 'brood' });

    await toggleTick(a);

    const row = await db.getItem(a.id);
    expect(row?.ticked).toBe(true); // instant IndexedDB persistence
    expect(row?.ticked_at).toBeTruthy();

    await loadStoresFromDb();
    const list = get(activeItems);
    expect(list.map((i) => i.id)).toEqual([b.id, a.id]); // unticked first, ticked below

    await setTick(a, false);
    expect((await db.getItem(a.id))?.ticked).toBe(false);
  });

  it('queues a tombstone mutation on manual delete and hides the row (FR-012)', async () => {
    const a = await addItem({ name: 'koffie', staple: true });
    await deleteItem(a.id);

    const row = await db.getItem(a.id);
    expect(row?.tombstone).toBe(true);
    expect(row?.staple).toBe(true); // staple flag survives manual deletion

    const outbox = await db.getOutbox();
    expect(outbox).toHaveLength(2); // add + tombstone, in order
    expect((outbox[1] as { item: { tombstone?: boolean } }).item.tombstone).toBe(true);

    await loadStoresFromDb();
    expect(get(activeItems)).toHaveLength(0);
  });

  it('queues a per-field edit mutation from the edit sheet (T029/T030)', async () => {
    const a = await addItem({ name: 'koffie', quantity: '2x' });
    await saveItemEdit(a, { name: 'koffie', quantity: '3x', note: null, staple: true });

    const outbox = await db.getOutbox();
    const edit = outbox[1] as unknown as { item: Record<string, unknown>; fields: string[] };
    expect(edit.fields).toEqual(['quantity', 'staple']);
    expect(edit.item.quantity).toBe('3x');
    expect(edit.item.staple).toBe(true);
    expect(edit.item.name).toBeUndefined(); // unchanged fields are not sent
  });

  it('X-delete hides the entry immediately, restores it on undo (T033)', async () => {
    const h = historyEntry({
      name: 'sauerkraut',
      occurred_at: new Date(Date.now() - 86400000).toISOString(),
    });
    await db.putHistory(h);
    await loadStoresFromDb();
    expect(get(historyVisible).map((e) => e.id)).toEqual([h.id]);

    await deleteHistoryEntry(h.id);
    expect(get(historyVisible)).toHaveLength(0); // optimistic hide
    expect(await db.getHistoryEntry(h.id)).toBeUndefined();
    expect(await db.getState<string[]>('pending_history_deletes')).toContain(h.id);

    await undoHistoryDelete(h.id);
    expect(get(historyVisible).map((e) => e.id)).toEqual([h.id]); // restored
    expect(await db.getState<string[]>('pending_history_deletes')).not.toContain(h.id);
  });

  it('fires the DELETE after the undo window; offline failures retry on the next sync', async () => {
    const h = historyEntry({ name: 'sauerkraut' });
    await db.putHistory(h);
    await loadStoresFromDb();

    // network down: DELETE cannot land
    vi.stubGlobal(
      'fetch',
      routeFetch({
        deleteHistory: () => {
          throw new TypeError('offline');
        },
      }),
    );
    await deleteHistoryEntry(h.id);
    await flushUndoTimersForTests();
    // quiet failure: still pending, retried on the next sync (FR-014)
    expect(await db.getState<string[]>('pending_history_deletes')).toContain(h.id);
    expect(await db.getHistoryEntry(h.id)).toBeUndefined(); // stays hidden meanwhile

    // connectivity back: sync completes the hard delete (FR-011)
    vi.stubGlobal(
      'fetch',
      routeFetch({
        deleteHistory: () => new Response(null, { status: 204 }),
        getHistory: () => Response.json([]),
        push: () => Response.json({ applied: [] }),
        pull: () => Response.json({ watermark: 2, items: [], history: [] }),
      }),
    );
    expect(await runSyncNow('test')).toBe('idle');
    expect(await db.getState<string[]>('pending_history_deletes')).not.toContain(h.id);
  });
});

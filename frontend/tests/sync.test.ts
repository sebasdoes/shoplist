import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as db from '../src/lib/db';
import {
  activeItems,
  loadStoresFromDb,
  runSyncNow,
  addItem,
  saveItemEdit,
} from '../src/lib/stores';
import { pullChanges, initSyncTriggers } from '../src/lib/sync';
import { routeFetch, seedItem, apiItemOf, configureTestApi } from './helpers';

beforeEach(configureTestApi);

describe('sync loop (T020/T021)', () => {
  it('flushes the outbox in order, adopts applied, persists the watermark', async () => {
    const a = await addItem({ name: 'Melk', quantity: '2x' });
    const b = await addItem({ name: 'Brood' });
    const c = await addItem({ name: 'Eieren 6x' });

    let pushBody: unknown;
    vi.stubGlobal(
      'fetch',
      routeFetch({
        push: (body) => {
          pushBody = body;
          const items = [a, b, c];
          return Response.json({
            applied: items.map((it, i) => ({
              id: it.id,
              rev: 100 + i,
              updated_at: '2026-11-21T00:00:00Z',
              merged_into: null,
            })),
          });
        },
        pull: () => Response.json({ watermark: 103, items: [], history: [] }),
      }),
    );

    const status = await runSyncNow('test');
    expect(status).toBe('idle');

    const mutations = (pushBody as { mutations: { op: string; item?: { id: string } }[] })
      .mutations;
    expect(mutations.map((m) => (m as { item: { id: string } }).item.id)).toEqual([
      a.id,
      b.id,
      c.id,
    ]);
    // all outbox entries cleared after confirmation
    expect(await db.getOutbox()).toHaveLength(0);
    expect(await db.getState<number>('watermark')).toBe(103);
  });

  it('retains the outbox and reports a quiet error when the push fails offline (FR-014)', async () => {
    await addItem({ name: 'Melk' });
    await addItem({ name: 'Brood' });

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('network down');
      }),
    );

    const status = await runSyncNow('test');
    expect(status).toBe('error');
    expect(await db.getOutbox()).toHaveLength(2); // retained for retry
  });

  it('recovers a retained queue on the next successful sync', async () => {
    await addItem({ name: 'Melk' });

    let ok = false;
    vi.stubGlobal(
      'fetch',
      routeFetch({
        push: (body) => {
          if (!ok) throw new TypeError('still offline');
          const muts = (body as { mutations: { item: { id: string } }[] }).mutations;
          return Response.json({
            applied: muts.map((m, i) => ({
              id: m.item.id,
              rev: 50 + i,
              updated_at: '2026-11-21T00:00:00Z',
              merged_into: null,
            })),
          });
        },
        pull: () => Response.json({ watermark: 51, items: [], history: [] }),
      }),
    );

    expect(await runSyncNow('first')).toBe('error');
    expect(await db.getOutbox()).toHaveLength(1);

    ok = true;
    expect(await runSyncNow('second')).toBe('idle');
    expect(await db.getOutbox()).toHaveLength(0);
  });

  it('adopts the merged_into survivor id and remaps queued edits (dedupe, T021)', async () => {
    const local = await addItem({ name: 'melk' }); // User B added it too, offline
    void local;

    // Simulate: push succeeded but folded into User B's surviving row.
    vi.stubGlobal(
      'fetch',
      routeFetch({
        push: () =>
          Response.json({
            applied: [
              {
                id: get(activeItems)[0].id,
                rev: 20,
                updated_at: '2026-11-21T00:00:00Z',
                merged_into: 'srv-1',
              },
            ],
          }),
        pull: () =>
          Response.json({
            watermark: 20,
            items: [
              {
                id: 'srv-1',
                name: 'melk',
                quantity: '2x',
                note: null,
                status: 'active',
                staple: false,
                tombstone: false,
                created_by: 'user-b',
                rev: 20,
              },
            ],
            history: [],
          }),
      }),
    );

    expect(await runSyncNow('test')).toBe('idle');
    await loadStoresFromDb();

    // exactly one active melk, under the survivor id
    const list = get(activeItems);
    expect(list).toHaveLength(1);
    expect(list[0].id).toBe('srv-1');
    expect(list[0].quantity).toBe('2x');
    expect(await db.getItem(local!.id)).toBeUndefined();

    // further edits target the survivor id (adoption of remote id)
    await saveItemEdit(list[0], { name: 'melk', quantity: '3x', note: null, staple: false });
    const outbox = await db.getOutbox();
    expect(outbox).toHaveLength(1);
    expect((outbox[0] as { item: { id: string; quantity?: string } }).item.id).toBe('srv-1');
    expect((outbox[0] as { item: { quantity?: string } }).item.quantity).toBe('3x');
  });

  it('pull upserts rows into IndexedDB and keeps local tick state on known rows', async () => {
    const existing = seedItem({ id: 'srv-1', name: 'koffie', ticked: true });
    await db.putItem(existing);
    await loadStoresFromDb();

    vi.stubGlobal(
      'fetch',
      routeFetch({
        pull: () =>
          Response.json({
            watermark: 30,
            items: [apiItemOf(seedItem({ id: 'srv-1', name: 'koffie', rev: 30 }))],
            history: [],
          }),
      }),
    );

    await pullChanges();
    const row = await db.getItem('srv-1');
    expect(row?.rev).toBe(30);
    expect(row?.ticked).toBe(true); // local tick survives the pull
    expect(await db.getState<number>('watermark')).toBe(30);
  });

  it('skips clobbering items covered by a locally queued trip decision', async () => {
    const item = seedItem({ id: 'srv-2', name: 'thee' });
    await db.putItem(item);
    // queue a trip "bought" for srv-2 (as completeTrip would)
    await db.enqueueOutbox({
      op: 'trip',
      trip: { id: crypto.randomUUID(), decisions: [{ item_id: 'srv-2', outcome: 'bought' }] },
      ts: new Date().toISOString(),
    });
    await db.putItem({ ...item, status: 'completed' }); // local transition

    vi.stubGlobal(
      'fetch',
      routeFetch({
        pull: () =>
          Response.json({ watermark: 40, items: [apiItemOf({ ...item, rev: 40 })], history: [] }),
      }),
    );

    await pullChanges();
    // local persisted transition is preserved until the server confirms the trip
    expect((await db.getItem('srv-2'))?.status).toBe('completed');
    expect((await db.getItem('srv-2'))?.rev).toBe(10);
  });

  it('registers the documented sync triggers without throwing in a bare environment', () => {
    expect(() => initSyncTriggers()).not.toThrow();
  });
});

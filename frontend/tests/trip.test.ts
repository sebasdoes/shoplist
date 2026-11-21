import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as db from '../src/lib/db';
import {
  addItem,
  completeTrip,
  loadStoresFromDb,
  runSyncNow,
  setTick,
  activeItems,
} from '../src/lib/stores';
import { routeFetch, seedItem, configureTestApi, activeNames } from './helpers';

beforeEach(configureTestApi);

// T026 — whole trip is ONE outbox unit; silent idempotent retry on sync.
describe('trip done (T026)', () => {
  it('bought/kept/drop land as a single queued trip mutation and apply locally', async () => {
    await addItem({ name: 'koffie', quantity: '2x', staple: true });
    await addItem({ name: 'thee' });
    await addItem({ name: 'zeep' });
    await addItem({ name: 'brood' });
    await loadStoresFromDb();

    const list = get(activeItems);
    const byName = (n: string) => list.find((i) => i.name === n)!;
    const koffie = byName('koffie');
    const thee = byName('thee');
    const zeep = byName('zeep');
    const brood = byName('brood');
    await setTick(koffie, true);
    await setTick(thee, true);

    const tripId = await completeTrip([
      { item_id: koffie.id, outcome: 'bought' },
      { item_id: thee.id, outcome: 'bought' },
      { item_id: zeep.id, outcome: 'kept' },
      { item_id: brood.id, outcome: 'not_bought' },
    ]);

    // trip id must be a client uuid (idempotency key)
    expect(tripId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);

    // exactly ONE outbox entry for the trip itself (adds were queued separately)
    const outbox = await db.getOutbox();
    const trips = outbox.filter((e) => e.op === 'trip');
    expect(trips).toHaveLength(1);
    const entry = trips[0] as {
      op: string;
      trip: { id: string; decisions: { item_id: string; outcome: string }[] };
    };
    expect(entry.trip.id).toBe(tripId);
    expect(entry.trip.decisions).toHaveLength(4);

    // local transitions: bought → completed (left the list), kept → active, drop → dropped
    expect((await db.getItem(koffie.id))?.status).toBe('completed');
    expect((await db.getItem(thee.id))?.status).toBe('completed');
    expect((await db.getItem(zeep.id))?.status).toBe('active');
    expect((await db.getItem(brood.id))?.status).toBe('dropped');

    await loadStoresFromDb();
    expect(activeNames()).toEqual(['zeep']);
  });

  it('a duplicate trip id comes back {"duplicate": true} and is simply cleared', async () => {
    const koffie = await addItem({ name: 'koffie', staple: true });
    await setTick(koffie, true);
    const tripId = await completeTrip([{ item_id: koffie.id, outcome: 'bought' }]);

    // Kim completed the same trip from her phone already.
    vi.stubGlobal(
      'fetch',
      routeFetch({
        push: () =>
          Response.json({
            applied: [],
            trip: { id: tripId, duplicate: true, readded: [] },
          }),
        pull: () => Response.json({ watermark: 21, items: [], history: [] }),
      }),
    );

    expect(await runSyncNow('test')).toBe('idle');
    // cleared: no trip entries remain (the add's upsert stays until confirmed)
    const left = await db.getOutbox();
    expect(left.filter((e) => e.op === 'trip')).toHaveLength(0);
  });

  it('an unconfirmed trip stays queued and is retried silently', async () => {
    const koffie = await addItem({ name: 'koffie', staple: true });
    await setTick(koffie, true);
    await completeTrip([{ item_id: koffie.id, outcome: 'bought' }]);

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('offline');
      }),
    );
    expect(await runSyncNow('test')).toBe('error');
    // trip unit retained intact for silent retry
    const retained = await db.getOutbox();
    expect(retained.filter((e) => e.op === 'trip')).toHaveLength(1);
    expect((await db.getItem(koffie.id))?.status).toBe('completed');
  });

  it('server re-added staples arrive via pull as new active rows', async () => {
    const koffie = await addItem({ name: 'koffie', quantity: '2x', staple: true });
    await setTick(koffie, true);
    await completeTrip([{ item_id: koffie.id, outcome: 'bought' }]);

    vi.stubGlobal(
      'fetch',
      routeFetch({
        push: () =>
          Response.json({
            applied: [],
            trip: { id: 't', duplicate: false, readded: [{ id: 'new-1', name: 'koffie' }] },
          }),
        pull: () =>
          Response.json({
            watermark: 50,
            items: [
              seedItem({ id: 'new-1', name: 'koffie', quantity: '2x', staple: true, rev: 50 }),
            ].map((i) => ({
              id: i.id,
              name: i.name,
              quantity: i.quantity,
              note: i.note,
              status: i.status,
              staple: i.staple,
              tombstone: i.tombstone,
              created_by: i.created_by,
              rev: i.rev,
            })),
            history: [],
          }),
      }),
    );

    expect(await runSyncNow('test')).toBe('idle');
    await loadStoresFromDb();
    expect(activeNames()).toEqual(['koffie']); // re-added as a NEW active row
    expect(get(activeItems)[0].id).toBe('new-1');
    expect(get(activeItems)[0].staple).toBe(true);
  });
});

import { beforeEach, describe, expect, it } from 'vitest';
import * as db from '../src/lib/db';
import type { Item, HistoryEntry } from '../src/lib/db';

function makeItem(overrides: Partial<Item> = {}): Item {
  const now = new Date().toISOString();
  return {
    id: overrides.id ?? crypto.randomUUID(),
    name: overrides.name ?? 'koffie',
    name_key: overrides.name_key ?? 'koffie',
    quantity: overrides.quantity ?? null,
    note: overrides.note ?? null,
    status: overrides.status ?? 'active',
    staple: overrides.staple ?? false,
    tombstone: overrides.tombstone ?? false,
    created_by: 'test',
    created_at: now,
    updated_at: now,
    rev: overrides.rev ?? 1,
    ticked: overrides.ticked ?? false,
    ticked_at: null,
    local_created_at: now,
    ...overrides,
  } as Item;
}

describe('db (T010)', () => {
  beforeEach(async () => {
    await db.resetForTests();
  });

  it('initializes and round-trips an item', async () => {
    await db.initDb();
    const item = makeItem();
    await db.putItem(item);
    const got = await db.getItem(item.id);
    expect(got?.name).toBe('koffie');
    expect(got?.staple).toBe(false);
  });

  it('keeps outbox entries in enqueue order (queue order == seq order)', async () => {
    await db.initDb();
    const e1 = await db.enqueueOutbox({
      op: 'upsert_item',
      item: { id: 'a', name: 'melk', list_id: 'list-1' },
      fields: ['name'],
      ts: new Date().toISOString(),
    });
    const e2 = await db.enqueueOutbox({
      op: 'upsert_item',
      item: { id: 'b', name: 'brood', list_id: 'list-1' },
      fields: ['name'],
      ts: new Date().toISOString(),
    });
    const e3 = await db.enqueueOutbox({
      op: 'trip',
      trip: { id: 't1', list_id: 'list-1', decisions: [] },
      ts: new Date().toISOString(),
    });

    const outbox = await db.getOutbox();
    expect(outbox.map((e) => e.seq)).toEqual([e1.seq, e2.seq, e3.seq]);
    expect(outbox[0].op).toBe('upsert_item');
    expect(outbox[2].op).toBe('trip');
  });

  it('deletes confirmed outbox seqs only', async () => {
    await db.initDb();
    const e1 = await db.enqueueOutbox({
      op: 'upsert_item',
      item: { id: 'a', list_id: 'list-1' },
      fields: [],
      ts: new Date().toISOString(),
    });
    await db.enqueueOutbox({
      op: 'upsert_item',
      item: { id: 'b', list_id: 'list-1' },
      fields: [],
      ts: new Date().toISOString(),
    });
    await db.deleteOutboxSeqs([e1.seq!]);
    const left = await db.getOutbox();
    expect(left).toHaveLength(1);
    expect((left[0] as { item: { id: string } }).item.id).toBe('b');
  });

  it('updates queued entries (used for merged_into remapping)', async () => {
    await db.initDb();
    const e = await db.enqueueOutbox({
      op: 'upsert_item',
      item: { id: 'old', name: 'melk', list_id: 'list-1' },
      fields: ['name'],
      ts: new Date().toISOString(),
    });
    await db.updateOutboxEntry({
      ...e,
      item: { ...(e as unknown as { item: Record<string, unknown> }).item, id: 'survivor' },
    } as typeof e);
    const outbox = await db.getOutbox();
    expect(outbox).toHaveLength(1);
    expect((outbox[0] as { item: { id: string } }).item.id).toBe('survivor');
    expect(outbox[0].seq).toBe(e.seq);
  });

  it('gets and sets arbitrary state (watermark, tokens)', async () => {
    await db.initDb();
    await db.setState('watermark', 142);
    await db.setState('session', { access_token: 'tok', sub: 'u1' });
    const wGot = await db.getState<number>('watermark');
    const sGot = await db.getState<{ sub: string }>('session');
    expect(wGot).toBe(142);
    expect(sGot?.sub).toBe('u1');
    expect(await db.getState('missing')).toBeNull();
  });

  it('stores and deletes history entries', async () => {
    await db.initDb();
    const h: HistoryEntry = {
      id: 'h1',
      item_id: 'i1',
      name: 'koffie',
      quantity: '2x',
      note: null,
      outcome: 'bought',
      staple_snapshot: true,
      actor: 'sub',
      occurred_at: new Date().toISOString(),
      rev: 10,
    };
    await db.putHistory(h);
    expect((await db.getAllHistory()).map((x) => x.id)).toEqual(['h1']);
    await db.deleteHistory('h1');
    expect(await db.getAllHistory()).toHaveLength(0);
  });

  it('clears everything (test isolation helper)', async () => {
    await db.initDb();
    await db.putItem(makeItem());
    await db.setState('watermark', 9);
    await db.clearStores();
    expect(await db.getAllItems()).toHaveLength(0);
    expect(await db.getState<number>('watermark')).toBeNull();
  });

  it('v1→v2 upgrade keeps queued outbox entries and adds the lists store once', async () => {
    // Start from a REAL v1 database (pre-lists schema), seeded with a queued
    // mutation, the way an app updated from the single-list build looks.
    await db.deleteDatabaseForTests();
    await new Promise<void>((resolve, reject) => {
      const req = indexedDB.open('shoplist', 1);
      req.onupgradeneeded = () => {
        const v1 = req.result;
        const items = v1.createObjectStore('items', { keyPath: 'id' });
        items.createIndex('by_rev', 'rev');
        items.createIndex('by_status', 'status');
        const history = v1.createObjectStore('history', { keyPath: 'id' });
        history.createIndex('by_occurred_at', 'occurred_at');
        history.createIndex('by_rev', 'rev');
        v1.createObjectStore('outbox', { keyPath: 'seq', autoIncrement: true });
        v1.createObjectStore('state');
      };
      req.onsuccess = () => {
        const v1 = req.result;
        const tx = v1.transaction('outbox', 'readwrite');
        tx.objectStore('outbox').add({
          op: 'upsert_item',
          item: { id: 'queued-a', name: 'melk', list_id: 'legacy-list' },
          fields: ['name'],
          ts: new Date().toISOString(),
        });
        tx.oncomplete = () => {
          v1.close();
          resolve();
        };
        tx.onerror = () => reject(tx.error);
      };
      req.onerror = () => reject(req.error);
    });

    // initDb opens at DB_VERSION=2 → the v1→v2 upgrade runs.
    await db.initDb();

    // 1. the queued mutation SURVIVED the migration (never dropped by an upgrade)
    const outbox = await db.getOutbox();
    expect(outbox).toHaveLength(1);
    expect(outbox[0].seq).toBe(1);
    expect((outbox[0] as { item: { id: string } }).item.id).toBe('queued-a');

    // 2. the lists store now exists exactly once (empty, usable)
    expect(await db.getAllLists()).toEqual([]);
    await db.putList({
      id: 'l1',
      name: 'Boodschappen',
      created_by: null,
      created_at: new Date().toISOString(),
      tombstone: false,
      rev: 0,
      sort_order: 0,
    });
    expect(await db.getAllLists()).toHaveLength(1);

    // 3. the watermark reset happened (state cleared → full resync next pull)
    expect(await db.getState('watermark')).toBeNull();

    // 4. the outbox keeps accepting new entries after the upgrade
    const e2 = await db.enqueueOutbox({
      op: 'upsert_item',
      item: { id: 'queued-b', name: 'brood', list_id: 'legacy-list' },
      fields: ['name'],
      ts: new Date().toISOString(),
    });
    expect(await db.getOutbox()).toHaveLength(2);
    expect(e2.seq).toBe(2);
  });
});

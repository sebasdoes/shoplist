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
      item: { id: 'a', name: 'melk' },
      fields: ['name'],
      ts: new Date().toISOString(),
    });
    const e2 = await db.enqueueOutbox({
      op: 'upsert_item',
      item: { id: 'b', name: 'brood' },
      fields: ['name'],
      ts: new Date().toISOString(),
    });
    const e3 = await db.enqueueOutbox({
      op: 'trip',
      trip: { id: 't1', decisions: [] },
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
      item: { id: 'a' },
      fields: [],
      ts: new Date().toISOString(),
    });
    await db.enqueueOutbox({
      op: 'upsert_item',
      item: { id: 'b' },
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
      item: { id: 'old', name: 'melk' },
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
});

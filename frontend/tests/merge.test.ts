import { describe, expect, it, vi } from 'vitest';
import * as db from '../src/lib/db';
import { saveItemEdit, runSyncNow, loadStoresFromDb } from '../src/lib/stores';
import { get } from 'svelte/store';
import { activeItems } from '../src/lib/stores';
import { pullChanges } from '../src/lib/sync';
import { routeFetch, seedItem, apiItemOf, configureTestApi } from './helpers';
import type { ApiItem } from '../src/lib/api';

// T031 — per-field edit merge (US3-2): User A's quantity edit (offline)
// and User B's note edit (synced) must BOTH survive. Client side mirrors the
// per-field LWW contract by overlaying pending fields onto fresher rows.
describe('per-field merge (T031)', () => {
  it('keeps the local quantity edit while accepting the remote note edit via pull', async () => {
    configureTestApi();
    const seeded = seedItem({
      id: 'srv-1',
      name: 'koffie',
      quantity: '1x',
      note: 'a',
      rev: 10,
    });
    await db.putItem(seeded);
    await db.setState('watermark', 10);
    await loadStoresFromDb();

    // User A edits quantity offline → queued per-field mutation
    await saveItemEdit(seeded, {
      name: 'koffie',
      quantity: '3x',
      note: 'a',
      staple: false,
    });
    const outbox = await db.getOutbox();
    expect(outbox).toHaveLength(1);
    expect((outbox[0] as { fields: string[] }).fields).toEqual(['quantity']);

    // Push cannot reach the server (offline) — but User B's note edit has already
    // landed there. Pull brings the fresher row back.
    vi.stubGlobal(
      'fetch',
      routeFetch({
        pull: () =>
          Response.json({
            watermark: 11,
            items: [
              apiItemOf({
                ...seeded,
                note: 'User B edit',
                quantity: '1x',
                rev: 11,
              }) as unknown as ApiItem,
            ],
            history: [],
          }),
      }),
    );

    await pullChanges();

    const row = await db.getItem('srv-1');
    expect(row?.quantity).toBe('3x'); // User A's edit survives (still queued)
    expect(row?.note).toBe('User B edit'); // User B's edit survives
    expect((await db.getOutbox()).length).toBeGreaterThanOrEqual(1);
  });

  it('after the outbox flush, the server-merged row carries both edits', async () => {
    configureTestApi();
    // Local pending edit: User A's quantity (pushed, confirmed by server).
    const item = seedItem({ id: 'srv-1', name: 'koffie', quantity: '1x', note: null, rev: 11 });
    await db.putItem(item);
    await db.setState('watermark', 11);
    await saveItemEdit(item, { name: 'koffie', quantity: '3x', note: null, staple: false });

    // Server merged: our pushed quantity edit (latest, per-field LWW) plus
    // User B's earlier note edit.
    const merged = apiItemOf(
      seedItem({ id: 'srv-1', name: 'koffie', quantity: '3x', note: 'User B edit', rev: 12 }),
    );
    vi.stubGlobal(
      'fetch',
      routeFetch({
        push: (body) => {
          // the queued per-field payload only contains the changed field(s)
          const muts = (body as { mutations: { item: Record<string, unknown> }[] }).mutations;
          expect(muts[0].item.quantity).toBe('3x');
          expect(mut0_check(muts[0].item)).toBe(true);
          return Response.json({
            applied: [
              { id: 'srv-1', rev: 12, updated_at: '2026-11-21T00:00:00Z', merged_into: null },
            ],
          });
        },
        pull: () => Response.json({ watermark: 12, items: [merged], history: [] }),
      }),
    );

    expect(await runSyncNow('test')).toBe('idle');
    expect(await db.getOutbox()).toHaveLength(0);

    const row = await db.getItem('srv-1');
    expect(row?.note).toBe('User B edit'); // User B's note edit survived
    expect(row?.quantity).toBe('3x'); // User A's quantity edit survived

    await loadStoresFromDb();
    expect(get(activeItems)[0].quantity).toBe('3x');
    expect(get(activeItems)[0].note).toBe('User B edit');
  });
});

function mut0_check(item: Record<string, unknown>): boolean {
  // unchanged fields must not be included in the pushed payload
  return item.name === undefined && item.staple === undefined;
}

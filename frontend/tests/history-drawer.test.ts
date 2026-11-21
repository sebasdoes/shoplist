import { render } from 'svelte/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import HistoryDrawer from '../src/components/HistoryDrawer.svelte';
import type { HistoryEntry } from '../src/lib/db';
import { deleteHistoryEntry, historyStore } from '../src/lib/stores';
import { configureTestApi } from './helpers';

beforeEach(configureTestApi);

function entry(overrides: Partial<HistoryEntry>): HistoryEntry {
  const now = new Date().toISOString();
  return {
    id: overrides.id ?? crypto.randomUUID(),
    item_id: overrides.item_id ?? crypto.randomUUID(),
    list_id: overrides.list_id ?? crypto.randomUUID(),
    name: overrides.name ?? 'Big American Texas',
    quantity: overrides.quantity ?? null,
    note: overrides.note ?? null,
    outcome: overrides.outcome ?? 'bought',
    staple_snapshot: overrides.staple_snapshot ?? 0,
    actor: overrides.actor ?? 'server',
    occurred_at: overrides.occurred_at ?? now,
  };
}

// pendingDeletes is not exported (internal to stores); drive it via deleteHistoryEntry.
describe('HistoryDrawer rendering', () => {
  it('renders purchase rows instead of the empty hint (regression: naked store in {#each})', () => {
    historyStore.set([
      entry({ name: 'Worst' }),
      entry({ name: 'Big American Texas', quantity: '2x' }),
    ]);
    const { html } = render(HistoryDrawer, { props: { onclose: vi.fn() } });

    expect(html).toContain('Worst');
    expect(html).toContain('Big American Texas');
    expect(html).toContain('2x');
    expect(html).not.toContain('No purchases yet');
  });

  it('still hides entries queued for delete', async () => {
    const e = entry({ name: 'Worst', occurred_at: new Date().toISOString() });
    historyStore.set([e]);
    // queued delete: optimistic remove is what hides the row locally
    await deleteHistoryEntry(e.id);
    const { html } = render(HistoryDrawer, { props: { onclose: vi.fn() } });

    expect(html).not.toContain('Worst');
    expect(html).toContain('No purchases yet');
  });
});

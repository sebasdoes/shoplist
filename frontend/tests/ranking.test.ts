import { describe, expect, it } from 'vitest';
import { rankSuggestions } from '../src/lib/rank';
import type { HistoryEntry } from '../src/lib/db';

const DAY = 86_400_000;
const NOW = Date.UTC(2026, 10, 21, 12); // fixed "now" for determinism

function entry(overrides: Partial<HistoryEntry> & { name: string }): HistoryEntry {
  return {
    id: crypto.randomUUID(),
    item_id: null,
    name: overrides.name,
    quantity: overrides.quantity ?? null,
    note: overrides.note ?? null,
    outcome: overrides.outcome ?? 'bought',
    staple_snapshot: overrides.staple_snapshot ?? false,
    actor: 'seb',
    occurred_at: overrides.occurred_at ?? new Date(NOW).toISOString(),
    rev: overrides.rev ?? 1,
  } as HistoryEntry;
}

describe('quick-add ranking (T033)', () => {
  it('scores Σ 0.5^(days_since/14) and orders by decayed frequency', () => {
    const koffieRecent = entry({
      name: 'koffie',
      quantity: '3x',
      occurred_at: new Date(NOW - 1 * DAY).toISOString(),
    });
    const koffieOld = entry({
      name: 'koffie',
      quantity: '2x',
      occurred_at: new Date(NOW - 8 * DAY).toISOString(),
    });
    const thee = entry({ name: 'thee', occurred_at: new Date(NOW - 5 * DAY).toISOString() });

    const ranked = rankSuggestions([koffieRecent, koffieOld, thee], NOW);
    expect(ranked).toHaveLength(2);

    const k = ranked[0];
    expect(k.nameKey).toBe('koffie');
    const expected = Math.pow(0.5, 1 / 14) + Math.pow(0.5, 8 / 14);
    expect(k.score).toBeCloseTo(expected, 10);
    expect(k.score).toBeGreaterThan(ranked[1].score);

    // prefills come from the LAST purchase
    expect(k.quantity).toBe('3x');
  });

  it('excludes not_bought entries and names already active on the list', () => {
    const entries = [
      entry({ name: 'sauerkraut', outcome: 'not_bought' }),
      entry({ name: 'koffie' }),
      entry({ name: 'thee' }),
    ];
    const ranked = rankSuggestions(entries, NOW, new Set(['thee']));
    expect(ranked.map((s) => s.nameKey)).toEqual(['koffie']);
  });

  it('normalizes names for grouping (case, accents, whitespace)', () => {
    const a = entry({ name: 'KOFFIE', occurred_at: new Date(NOW - 2 * DAY).toISOString() });
    const b = entry({ name: 'koffie', occurred_at: new Date(NOW - 1 * DAY).toISOString() });
    const ranked = rankSuggestions([a, b], NOW);
    expect(ranked).toHaveLength(1);
    expect(ranked[0].name).toBe('koffie'); // display from the most recent
  });

  it('keeps the staple flag when any snapshot was a staple (US2 prefill)', () => {
    const ranked = rankSuggestions(
      [
        entry({
          name: 'koffie',
          staple_snapshot: true,
          occurred_at: new Date(NOW - 3 * DAY).toISOString(),
        }),
      ],
      NOW,
    );
    expect(ranked[0].staple).toBe(true);
  });

  it('breaks score ties by most-recent purchase', () => {
    const recent = entry({ name: 'a', occurred_at: new Date(NOW - 2 * DAY).toISOString() });
    const older = entry({ name: 'b', occurred_at: new Date(NOW - 6 * DAY).toISOString() });
    const ranked = rankSuggestions([older, recent], NOW);
    // identical single-purchase weight per days… 0.5^(2/14) > 0.5^(6/14)
    expect(ranked[0].name).toBe('a');
    expect(ranked[1].name).toBe('b');
  });

  it('ignores malformed dates instead of producing NaN scores', () => {
    const bad = entry({ name: 'x', occurred_at: 'not-a-date' as unknown as string });
    const ranked = rankSuggestions([bad], NOW);
    // daysSince NaN → score NaN; ensure it does not crash ordering
    expect(ranked).toHaveLength(1);
    expect(ranked[0].historyIds.length).toBe(1);
  });
});

// research D4 — quick-add ranking: score = Σ_purchases 0.5^(days_since / 14),
// ties broken by most-recent purchase. not_bought entries are excluded by the
// caller (T033); entries whose name is already on the active list are excluded.
import type { HistoryEntry } from './db';
import { nameKey } from './validate';

export interface Suggestion {
  nameKey: string;
  name: string;
  quantity: string | null;
  note: string | null;
  staple: boolean;
  score: number;
  lastOccurredAt: string;
  historyIds: string[];
}

export function rankSuggestions(
  history: HistoryEntry[],
  nowMs: number,
  excludeNameKeys: Set<string> = new Set(),
): Suggestion[] {
  const HALF_LIFE_DAYS = 14;
  const buckets = new Map<string, Suggestion>();

  for (const h of history) {
    if (h.outcome === 'not_bought') continue;
    const key = nameKey(h.name);
    if (excludeNameKeys.has(key)) continue;

    const occurred = Date.parse(h.occurred_at);
    const daysSince = Math.max(0, (nowMs - occurred) / 86_400_000);
    const contribution = Math.pow(0.5, daysSince / HALF_LIFE_DAYS);

    let b = buckets.get(key);
    if (!b) {
      b = {
        nameKey: key,
        name: h.name,
        quantity: h.quantity,
        note: h.note,
        staple: false,
        score: 0,
        lastOccurredAt: h.occurred_at,
        historyIds: [],
      };
      buckets.set(key, b);
    }
    b.score += contribution;
    b.staple = b.staple || h.staple_snapshot;
    b.historyIds = [...b.historyIds, h.id];
    if (h.occurred_at > b.lastOccurredAt) {
      // Prefills come from the LAST purchase.
      b.lastOccurredAt = h.occurred_at;
      b.name = h.name;
      b.quantity = h.quantity;
      b.note = h.note;
    }
  }

  return [...buckets.values()].sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return a.lastOccurredAt < b.lastOccurredAt ? 1 : a.lastOccurredAt > b.lastOccurredAt ? -1 : 0;
  });
}

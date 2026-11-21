// T028/T033 foundation — history persistence + hard-delete (FR-011).
// X-delete is hard delete with no tombstone (documented constitution-I
// exception). If the DELETE cannot reach the server, the id stays in
// `state.pending_history_deletes` and is retried by every sync run; the entry
// stays hidden locally meanwhile (quiet, no dialogs — FR-014).
import { apiDeleteHistory, apiGetHistory, ApiError, type ApiHistoryEntry } from './api';
import * as db from './db';
import type { HistoryEntry } from './db';

export const PENDING_DELETES_KEY = 'pending_history_deletes';

export async function getPendingDeletes(): Promise<Set<string>> {
  const list = await db.getState<string[]>(PENDING_DELETES_KEY);
  return new Set(list ?? []);
}

async function addPendingDelete(id: string): Promise<void> {
  const pending = await getPendingDeletes();
  pending.add(id);
  await db.setState(PENDING_DELETES_KEY, [...pending]);
}

async function removePendingDelete(id: string): Promise<void> {
  const pending = await getPendingDeletes();
  pending.delete(id);
  await db.setState(PENDING_DELETES_KEY, [...pending]);
}

/** Optimistic local remove + record for later server delete. */
export async function removeHistoryLocally(entry: HistoryEntry): Promise<void> {
  await db.deleteHistory(entry.id);
  await addPendingDelete(entry.id);
}

/** Undo: put the row back and cancel the pending server delete. */
export async function restoreHistory(entry: HistoryEntry): Promise<void> {
  await db.putHistory(entry);
  await removePendingDelete(entry.id);
}

/** Retry outstanding history DELETEs. Throws on network failure (sync marks chip). */
export async function flushHistoryDeletes(): Promise<void> {
  const pending = await getPendingDeletes();
  for (const id of pending) {
    try {
      await apiDeleteHistory(id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        await removePendingDelete(id); // already gone server-side
        continue;
      }
      throw err; // network/unauthorized: keep pending, retry next sync
    }
    await removePendingDelete(id);
  }
}

/** GET /history refresh path: full reconcile (server is authoritative). */
export async function reconcileHistoryFromServer(): Promise<void> {
  const rows: ApiHistoryEntry[] = await apiGetHistory();
  const pending = await getPendingDeletes();
  const serverIds = new Set<string>();

  for (const row of rows) {
    serverIds.add(row.id);
    if (pending.has(row.id)) continue; // deleted locally, delete is queued
    const existing = await db.getHistoryEntry(row.id);
    if (!existing || existing.rev <= row.rev) {
      await db.putHistory(apiHistoryToLocal(row));
    }
  }

  // Prune rows the server no longer has (X-deleted by the other device).
  const local = await db.getAllHistory();
  for (const entry of local) {
    if (!serverIds.has(entry.id) && !pending.has(entry.id)) {
      await db.deleteHistory(entry.id);
    }
  }
}

export function apiHistoryToLocal(row: ApiHistoryEntry): HistoryEntry {
  return {
    id: row.id,
    item_id: null,
    name: row.name,
    quantity: row.quantity,
    note: row.note,
    outcome: row.outcome,
    staple_snapshot: false,
    actor: row.actor,
    occurred_at: row.occurred_at,
    rev: row.rev,
  };
}

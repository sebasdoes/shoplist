// T020 — sync loop per contracts/api.md client sync loop:
//   1. reads render from IndexedDB (stores.ts); 2. push outbox (adopt applied,
//   handle merged_into, clear pushed); 3. pull with watermark; 4. triggers:
//   online, focus, 60 s visible timer, debounced 2 s post-mutation;
//   5. failure never throws to the UI — outbox retained + quiet chip (FR-014).
import { apiPull, apiPush, type ApiItem } from './api';
import * as db from './db';
import type { Item, OutboxStored } from './db';
import { nameKey } from './validate';
import {
  flushHistoryDeletes,
  reconcileHistoryFromServer,
  getPendingDeletes,
  apiHistoryToLocal,
} from './history';

export type SyncStatusValue = 'idle' | 'syncing' | 'offline' | 'error';

let currentStatus: SyncStatusValue = 'idle';
let statusConsumer: ((s: SyncStatusValue) => void) | null = null;
export function onSyncStatus(fn: (s: SyncStatusValue) => void): void {
  statusConsumer = fn;
  fn(currentStatus);
}

function setStatus(s: SyncStatusValue): void {
  currentStatus = s;
  statusConsumer?.(s);
}

const syncCompleteListeners: (() => void)[] = [];
export function onSyncComplete(fn: () => void): void {
  syncCompleteListeners.push(fn);
}
function emitComplete(): void {
  for (const fn of [...syncCompleteListeners]) fn();
}

let inFlight = false;
export function isSyncing(): boolean {
  return inFlight;
}

export async function runSync(_reason = 'manual'): Promise<void> {
  if (inFlight) return;
  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    setStatus('offline');
    return;
  }
  inFlight = true;
  setStatus('syncing');
  try {
    // 2. push outbox
    await flushOutbox();
    // 3. pull watermark upsert
    await pullChanges();
    // History foundation (T028): full history reconcile + queued X-deletes.
    await reconcileHistoryFromServer();
    await flushHistoryDeletes();
    setStatus('idle');
    emitComplete();
  } catch {
    // Quiet: outbox retained, chip flips to "syncing…" — never an error dialog.
    setStatus('error');
  } finally {
    inFlight = false;
  }
}

// ---- push ----

type UpsertEntry = Extract<OutboxStored, { op: 'upsert_item' }>;

async function flushOutbox(): Promise<void> {
  const entries = await db.getOutbox();
  if (entries.length === 0) return;

  const res = await apiPush({
    mutations: entries.map((e) =>
      e.op === 'upsert_item' ? { op: 'upsert_item', item: e.item } : { op: 'trip', trip: e.trip },
    ),
  });

  const appliedById = new Map(res.applied.map((a) => [a.id, a]));
  const seqsDone: number[] = [];

  for (const entry of entries) {
    if (entry.op === 'trip') {
      // Duplicate trip id → {"duplicate": true}, no effects: clear from outbox.
      seqsDone.push(entry.seq);
      continue;
    }
    const applied = appliedById.get(entry.item.id);
    if (applied) {
      if (applied.merged_into) {
        // Dedupe hit: folded into the existing active item — adopt the survivor.
        await adoptSurvivor(entry.item.id, applied.merged_into);
      }
      seqsDone.push(entry.seq);
    }
    // No applied entry → not confirmed; keep queued for retry.
  }

  await db.deleteOutboxSeqs(seqsDone);
}

/** merged_into adoption: local row is re-keyed onto the server survivor; queued
 * mutations referencing the old client id are remapped (T021). */
async function adoptSurvivor(oldId: string, survivorId: string): Promise<void> {
  const local = await db.getItem(oldId);
  if (local) {
    const adopted: Item = { ...local, id: survivorId, name_key: nameKey(local.name) };
    await db.putItem(adopted);
    await db.deleteItemDoc(oldId);
  }
  const entries = await db.getOutbox();
  for (const e of entries) {
    if (e.op === 'upsert_item' && e.item.id === oldId) {
      await db.updateOutboxEntry({ ...e, item: { ...e.item, id: survivorId } });
    }
  }
}

// ---- pull ----

function apiItemToLocal(row: ApiItem, existing?: Item): Item {
  const now = new Date().toISOString();
  return {
    id: row.id,
    name: row.name,
    name_key: nameKey(row.name),
    quantity: row.quantity,
    note: row.note,
    status: row.status,
    staple: row.staple,
    tombstone: row.tombstone,
    created_by: row.created_by,
    // Pull payload carries no timestamps (they are server-stamped server-side);
    // keep existing local copies so local ordering doesn't jump.
    created_at: existing?.created_at ?? '',
    updated_at: existing?.updated_at ?? '',
    rev: row.rev,
    ticked: existing?.ticked ?? false,
    ticked_at: existing?.ticked_at ?? null,
    local_created_at: existing?.local_created_at ?? now,
  };
}

/** Apply locally-pending field edits (per-field) over fresher server truth. */
function overlayPendingFields(server: Item, pendingUpserts: UpsertEntry[]): Item {
  const row: Item = { ...server };
  for (const e of pendingUpserts) {
    for (const field of e.fields) {
      if (field === 'name' && e.item.name !== undefined) {
        row.name = e.item.name;
        row.name_key = nameKey(e.item.name);
      } else if (field === 'quantity' && e.item.quantity !== undefined) {
        row.quantity = e.item.quantity;
      } else if (field === 'note' && e.item.note !== undefined) {
        row.note = e.item.note;
      } else if (field === 'staple' && e.item.staple !== undefined) {
        row.staple = e.item.staple;
      } else if (field === 'tombstone' && e.item.tombstone !== undefined) {
        row.tombstone = e.item.tombstone;
      }
    }
  }
  return row;
}

export async function pullChanges(): Promise<void> {
  const watermark = (await db.getState<number>('watermark')) ?? 0;
  const res = await apiPull({ watermark, include_history: true });

  const pending = await db.getOutbox();
  const pendingById = new Map<string, UpsertEntry[]>();
  const tripItemIds = new Set<string>();
  for (const e of pending) {
    if (e.op === 'upsert_item') {
      const list = pendingById.get(e.item.id) ?? [];
      list.push(e as UpsertEntry);
      pendingById.set(e.item.id, list);
    } else {
      for (const d of e.trip.decisions) tripItemIds.add(d.item_id);
    }
  }

  const pendingDeletes = await getPendingDeletes();

  for (const row of res.items) {
    const existing = await db.getItem(row.id);

    // Items with locally persisted trip decisions (bought/kept/drop) keep
    // their local transition until the server confirms the queued trip.
    if (tripItemIds.has(row.id)) continue;

    // Tombstone rows are stored as-is (UI hides them); status preserved.
    let local = apiItemToLocal(row, existing);

    // Un-confirmed local edits survive the pull per-field (research D2);
    // the server merge still lands once the outbox entry is accepted.
    const pendingUpserts = pendingById.get(row.id);
    if (pendingUpserts && pendingUpserts.length > 0) {
      local = overlayPendingFields(local, pendingUpserts);
    }
    await db.putItem(local);
  }

  // History rows from pull land in IndexedDB `history` store (T028).
  for (const h of res.history ?? []) {
    if (pendingDeletes.has(h.id)) continue; // hidden + delete queued
    await db.putHistory(apiHistoryToLocal(h));
  }

  await db.setState('watermark', res.watermark);
}

// ---- triggers ----

let postMutationTimer: ReturnType<typeof setTimeout> | null = null;

/** Trigger 4: after any local mutation, debounced 2 s (contracts/api.md). */
export function schedulePostMutationSync(): void {
  if (typeof setTimeout === 'undefined') return;
  if (postMutationTimer) clearTimeout(postMutationTimer);
  postMutationTimer = setTimeout(() => {
    postMutationTimer = null;
    void runSync('mutation');
  }, 2000);
}

/** Flushes the outbox via Background Sync when the browser supports it. */
export async function requestBackgroundSync(): Promise<void> {
  try {
    if (typeof navigator !== 'undefined' && navigator.serviceWorker) {
      const reg = await navigator.serviceWorker.ready;
      const sync = (reg as unknown as { sync?: { register: (tag: string) => Promise<void> } }).sync;
      if (sync) await sync.register('shoplist-sync');
    }
  } catch {
    /* best effort — regular triggers still run */
  }
}

/** Triggers: online event, focus, 60 s visible timer, background-sync poke. */
export function initSyncTriggers(): void {
  if (typeof window === 'undefined') return;

  window.addEventListener('online', () => void runSync('online'));
  window.addEventListener('focus', () => void runSync('focus'));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void runSync('visible');
  });
  window.setInterval(() => {
    if (document.visibilityState === 'visible') void runSync('timer60s');
  }, 60_000);

  if (navigator.serviceWorker) {
    navigator.serviceWorker.addEventListener('message', (event) => {
      const data = (event as MessageEvent).data;
      if (data?.type === 'SHOPLIST_SYNC_TRIGGER') void runSync('bg-sync');
    });
  }
}

export function clearPostMutationTimerForTests(): void {
  if (postMutationTimer) {
    clearTimeout(postMutationTimer);
    postMutationTimer = null;
  }
}

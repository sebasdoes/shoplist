// T020 — sync loop per contracts/api.md client sync loop:
//   1. reads render from IndexedDB (stores.ts); 2. push outbox (adopt applied,
//   handle merged_into, clear pushed); 3. pull with watermark; 4. triggers:
//   online, focus, 60 s visible timer, debounced 2 s post-mutation;
//   5. failure never throws to the UI — outbox retained + quiet chip (FR-014).
import { apiPull, apiPush, type ApiItem, type AppliedEntry, type AppliedList } from './api';
import * as db from './db';
import type { Item, OutboxStored } from './db';
import { config } from './config';
import { logBreadcrumb } from './log';
import * as oidc from './oidc';
import { nameKey } from './validate';
import { reconcileActiveList } from './active-list';
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
    mutations: entries.map((e) => {
      if (e.op === 'upsert_item') return { op: 'upsert_item', item: e.item };
      if (e.op === 'trip') return { op: 'trip', trip: e.trip };
      if (e.op === 'create_list') return { op: 'create_list', list: e.list };
      // feature-003 — the full ordered id array rides as ONE op (schema v3)
      if (e.op === 'reorder_lists') return { op: 'reorder_lists', order: e.order };
      // feature-004 — flat wire shape: { list_id, order: [item ids] } (schema v4)
      if (e.op === 'reorder_items') return { op: 'reorder_items', list_id: e.list_id, order: e.order };
      return { op: 'delete_list', list_id: e.list_id };
    }),
  });

  // feature-004 poison fix — entries the server REJECTED are dropped from the
  // outbox (they could never apply) and beaconed for diagnosis. `rejected`
  // indexes match the mutations array, which is built 1:1 from `entries`.
  if (res.rejected && res.rejected.length > 0) {
    for (const rj of res.rejected) {
      const seq = entries[rj.index]?.seq;
      if (seq !== undefined) {
        await db.deleteOutboxSeqs([seq]);
        void logBreadcrumb(`push-rejected ${rj.op}: ${rj.detail}`);
      }
    }
  }

  // T011 — the push response may carry a `lists` section alongside `applied`
  // (`applied` gains `lists` per contracts/api.md). Adopt server revs/tombstones.
  const appliedLists = extractAppliedLists(res);
  const appliedListIds = new Set((appliedLists ?? []).map((l) => l.id));
  for (const al of appliedLists ?? []) {
    const existing = await db.getList(al.id);
    await db.putList({
      id: al.id,
      name: al.name,
      rev: al.rev,
      tombstone: al.tombstone,
      // feature-003: the v3 echo carries sort_order — persist it (order is
      // household-wide truth; pre-v3 rows keep whatever they had).
      sort_order: al.sort_order ?? existing?.sort_order ?? 0,
      created_by: existing?.created_by ?? null,
      created_at: existing?.created_at ?? '',
    });
  }

  // Applied group canonical shape (server): { items: [...], lists: [...] }.
  // Tolerate the legacy bare-array shape defensively — never crash the outbox.
  const appliedItems: AppliedEntry[] = Array.isArray(res.applied)
    ? (res.applied as unknown as AppliedEntry[])
    : (res.applied?.items ?? []);
  const appliedById = new Map(appliedItems.map((a) => [a.id, a]));
  const seqsDone: number[] = [];

  for (const entry of entries) {
    if (entry.op === 'trip') {
      // Duplicate trip id → {"duplicate": true}, no effects: clear from outbox.
      seqsDone.push(entry.seq);
      continue;
    }
    if (entry.op === 'create_list') {
      // confirmed when the response echoes the list (best-effort if absent)
      if (appliedLists === undefined || appliedListIds.has(entry.list.id)) {
        seqsDone.push(entry.seq);
      }
      continue;
    }
    if (entry.op === 'delete_list') {
      if (appliedLists === undefined || appliedListIds.has(entry.list_id)) {
        seqsDone.push(entry.seq);
      }
      continue;
    }
    if (entry.op === 'reorder_lists') {
      // feature-003 — confirmed by the applied.lists echo (whose adoption
      // above also re-persists the server's sort_order on every row).
      if (appliedLists === undefined || appliedLists.length > 0) {
        seqsDone.push(entry.seq);
      }
      continue;
    }
    if (entry.op === 'reorder_items') {
      // feature-004 — confirmed by the applied.items echo: the server echoes
      // the reordered rows with fresh revs. Until then the op stays queued.
      if (entry.order.some((id) => appliedById.has(id))) {
        seqsDone.push(entry.seq);
      }
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

  // feature-004 — reorder echo adoption: the reordered rows come back in
  // applied.items with fresh revs (and, from v4 servers, the assigned
  // sort_order). Persist rank + rev WITHOUT overwriting other local fields.
  const reorderedEchoes = new Set(
    entries.flatMap((e) => (e.op === 'reorder_items' ? e.order : [])),
  );
  if (reorderedEchoes.size > 0) {
    for (const a of appliedItems) {
      if (a.merged_into || !reorderedEchoes.has(a.id)) continue;
      const existing = await db.getItem(a.id);
      if (!existing) continue;
      const merged: Item = { ...existing };
      // sort_order is server-assigned on reorder — always adopt it on the echo
      if (a.sort_order !== undefined) merged.sort_order = a.sort_order;
      if (a.rev > merged.rev) {
        merged.rev = a.rev;
        if (a.updated_at) merged.updated_at = a.updated_at;
      }
      await db.putItem(merged);
    }
  }

  if (appliedLists && appliedLists.length > 0) await reconcileActiveList();
}

/** The `lists` section of the push response: `undefined` when the server sent
 * none (then list ops confirm best-effort), `[]` when explicitly empty. */
function extractAppliedLists(res: {
  applied?: { lists?: AppliedList[] };
}): AppliedList[] | undefined {
  const nested = (res as { applied?: { lists?: AppliedList[] } }).applied?.lists;
  if (Array.isArray(nested)) return nested;
  return undefined;
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
    list_id: row.list_id ?? existing?.list_id ?? db.DEFAULT_LIST_ID,
    // feature-004 (schema v4): the pull row carries the server-assigned order;
    // absent (pre-v4 server) → keep the local value, else 0.
    sort_order: row.sort_order ?? existing?.sort_order ?? 0,
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

  // T011 — the pull applies `lists` into the lists store FIRST (a watermark-0
  // snapshot includes ALL lists, tombstoned ones too, so devices can retire
  // their active selection). The watermark mechanism itself is unchanged.
  for (const l of res.lists ?? []) {
    const existing = await db.getList(l.id);
    await db.putList({
      id: l.id,
      name: l.name,
      rev: l.rev,
      tombstone: l.tombstone,
      // feature-003: pull rows carry sort_order (schema v3) — persist it;
      // absent (pre-v3 server) → keep the local value, else 0.
      sort_order: l.sort_order ?? existing?.sort_order ?? 0,
      created_by: existing?.created_by ?? null,
      created_at: existing?.created_at ?? '',
    });
  }

  const pending = await db.getOutbox();
  const pendingById = new Map<string, UpsertEntry[]>();
  const tripItemIds = new Set<string>();
  for (const e of pending) {
    if (e.op === 'upsert_item') {
      const list = pendingById.get(e.item.id) ?? [];
      list.push(e as UpsertEntry);
      pendingById.set(e.item.id, list);
    } else if (e.op === 'trip') {
      for (const d of e.trip.decisions) tripItemIds.add(d.item_id);
    }
    // create_list / delete_list entries carry no item rows to protect
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

  // FR-020 — when the stored selection is tombstoned or missing, fall back to
  // the first live list and persist that fallback.
  await reconcileActiveList();
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

/** Triggers: online event, focus, 5 s visible timer, SSE server push, bg-sync. */
export function initSyncTriggers(): void {
  if (typeof window === 'undefined') return;

  window.addEventListener('online', () => void runSync('online'));
  window.addEventListener('focus', () => void runSync('focus'));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void runSync('visible');
  });
  // SC-002 wants partner edits visible within seconds; SSE is the fast path,
  // this 5 s poll is the fallback (e.g. EventSource refused).
  window.setInterval(() => {
    if (document.visibilityState === 'visible') void runSync('timer5s');
  }, 5_000);

  if (navigator.serviceWorker) {
    navigator.serviceWorker.addEventListener('message', (event) => {
      const data = (event as MessageEvent).data;
      if (data?.type === 'SHOPLIST_SYNC_TRIGGER') void runSync('bg-sync');
    });
  }

  void startEventStream();
}

// ---- SSE server push (fast path for instant cross-device updates) ----

let streamStarted = false;

async function startEventStream(): Promise<void> {
  if (streamStarted || typeof EventSource === 'undefined' || !navigator.onLine) return;
  streamStarted = true;
  for (;;) {
    let waitMs = 5000;
    try {
      const token = await oidc.ensureFreshToken();
      if (token) {
        await openEventStream(token);
      } else {
        waitMs = 10000; // signed out; the sign-in flow will re-enter
      }
    } catch {
      /* retry with backoff below */
    }
    if (!navigator.onLine) waitMs = 15000;
    await sleep(waitMs);
  }
}

function openEventStream(token: string): Promise<void> {
  return new Promise<void>((resolve) => {
    const es = new EventSource(`${config.apiBase}/events?token=${encodeURIComponent(token)}`);
    const finish = () => {
      try {
        es.close();
      } catch {
        /* already closed */
      }
      resolve();
    };
    es.onopen = () => undefined; // connected; messages follow
    es.onmessage = (ev) => {
      try {
        const payload = (ev as MessageEvent).data as string;
        if (payload === 'changed') void runSync('server-push');
      } catch {
        /* ignore malformed frames */
      }
    };
    es.onerror = finish; // EventSource cannot recover its token; reopen fresh
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export function clearPostMutationTimerForTests(): void {
  if (postMutationTimer) {
    clearTimeout(postMutationTimer);
    postMutationTimer = null;
  }
}

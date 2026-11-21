// T010 — IndexedDB per data-model.md client sync_state.
// Stores: items, history, outbox (mutations or trip events with client uuids),
// state (watermark, tokens, pending flags).
import { openDB, type DBSchema, type IDBPDatabase } from 'idb';

export type ItemStatus = 'active' | 'completed' | 'dropped';

export interface Item {
  id: string;
  name: string;
  name_key: string;
  quantity: string | null;
  note: string | null;
  status: ItemStatus;
  staple: boolean;
  tombstone: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
  rev: number;
  // Local-only conveniences (never sent to the server):
  ticked: boolean;
  ticked_at: string | null;
  local_created_at: string; // client ordering only, not a conflict source
}

export interface HistoryEntry {
  id: string;
  item_id: string | null;
  name: string;
  quantity: string | null;
  note: string | null;
  outcome: 'bought' | 'not_bought';
  staple_snapshot: boolean;
  actor: string;
  occurred_at: string;
  rev: number;
}

export type TripOutcome = 'bought' | 'kept' | 'not_bought';

export interface TripDecision {
  item_id: string;
  outcome: TripOutcome;
}

export interface PushTrip {
  id: string;
  decisions: TripDecision[];
}

/** Item payload shaped exactly for POST /sync/push `upsert_item` (contracts/api.md). */
export interface OutboxItemMutation {
  id: string;
  name?: string;
  quantity?: string | null;
  note?: string | null;
  staple?: boolean;
  tombstone?: boolean;
}

export type OutboxEntry =
  | {
      op: 'upsert_item';
      item: OutboxItemMutation;
      /** which fields this mutation changes — used for per-field conflict merge (research D2) */
      fields: string[];
      ts: string;
      seq?: number;
    }
  | {
      op: 'trip';
      trip: PushTrip;
      ts: string;
      seq?: number;
    };

export type OutboxStored = OutboxEntry & { seq: number };

interface ShoplistDB extends DBSchema {
  items: {
    key: string;
    value: Item;
    indexes: { by_rev: number; by_status: string };
  };
  history: {
    key: string;
    value: HistoryEntry;
    indexes: { by_occurred_at: string; by_rev: number };
  };
  outbox: {
    key: number; // auto-increment seq == queue order
    value: OutboxStored;
  };
  state: {
    key: string;
    value: unknown;
  };
}

const DB_NAME = 'shoplist';
const DB_VERSION = 1;

let dbp: Promise<IDBPDatabase<ShoplistDB>> | null = null;

export function initDb(): Promise<IDBPDatabase<ShoplistDB>> {
  if (!dbp) {
    dbp = openDB<ShoplistDB>(DB_NAME, DB_VERSION, {
      upgrade(db) {
        const items = db.createObjectStore('items', { keyPath: 'id' });
        items.createIndex('by_rev', 'rev');
        items.createIndex('by_status', 'status');

        const history = db.createObjectStore('history', { keyPath: 'id' });
        history.createIndex('by_occurred_at', 'occurred_at');
        history.createIndex('by_rev', 'rev');

        db.createObjectStore('outbox', { keyPath: 'seq', autoIncrement: true });
        db.createObjectStore('state');
      },
    });
  }
  return dbp;
}

async function conn(): Promise<IDBPDatabase<ShoplistDB>> {
  return initDb();
}

// ---- items ----

export async function getAllItems(): Promise<Item[]> {
  return (await conn()).getAll('items');
}

export async function getItem(id: string): Promise<Item | undefined> {
  return (await conn()).get('items', id);
}

export async function putItem(item: Item): Promise<void> {
  const db = await conn();
  await db.put('items', item);
}

export async function putItems(items: Item[]): Promise<void> {
  const db = await conn();
  const tx = db.transaction('items', 'readwrite');
  for (const item of items) tx.store.put(item);
  await tx.done;
}

export async function deleteItemDoc(id: string): Promise<void> {
  const db = await conn();
  await db.delete('items', id);
}

// ---- history ----

export async function getAllHistory(): Promise<HistoryEntry[]> {
  return (await conn()).getAll('history');
}

export async function getHistoryEntry(id: string): Promise<HistoryEntry | undefined> {
  return (await conn()).get('history', id);
}

export async function putHistory(entry: HistoryEntry): Promise<void> {
  const db = await conn();
  await db.put('history', entry);
}

export async function deleteHistory(id: string): Promise<void> {
  const db = await conn();
  await db.delete('history', id);
}

// ---- outbox (queue order = seq order; mutations persist BEFORE network) ----

export async function enqueueOutbox(entry: OutboxEntry): Promise<OutboxStored> {
  const db = await conn();
  const seq = await db.add('outbox', entry as OutboxStored);
  return { ...entry, seq } as OutboxStored;
}

/** All queued entries in enqueue order. */
export async function getOutbox(): Promise<OutboxStored[]> {
  const db = await conn();
  const all = await db.getAll('outbox');
  return all.sort((a, b) => a.seq - b.seq);
}

export async function updateOutboxEntry(entry: OutboxStored): Promise<void> {
  const db = await conn();
  await db.put('outbox', entry);
}

export async function deleteOutboxSeqs(seqs: number[]): Promise<void> {
  if (seqs.length === 0) return;
  const db = await conn();
  const tx = db.transaction('outbox', 'readwrite');
  for (const seq of seqs) tx.store.delete(seq);
  await tx.done;
}

// ---- state (watermark, tokens, pending flags) ----

export async function getState<T>(key: string): Promise<T | null> {
  const db = await conn();
  const value = await db.get('state', key);
  return (value ?? null) as T | null;
}

export async function setState(key: string, value: unknown): Promise<void> {
  const db = await conn();
  await db.put('state', value as never, key);
}

export async function deleteState(key: string): Promise<void> {
  const db = await conn();
  await db.delete('state', key);
}

// ---- test helpers ----

export async function clearStores(): Promise<void> {
  const db = await conn();
  const tx = db.transaction(['items', 'history', 'outbox', 'state'], 'readwrite');
  tx.objectStore('items').clear();
  tx.objectStore('history').clear();
  tx.objectStore('outbox').clear();
  tx.objectStore('state').clear();
  await tx.done;
}

export async function resetForTests(): Promise<void> {
  if (dbp) {
    const closed = dbp.then((db) => db.close());
    await closed;
    dbp = null;
  }
  await new Promise<void>((resolve, reject) => {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
    req.onblocked = () => reject(new Error('deleteDatabase blocked — leaked connection'));
  });
  await initDb();
}

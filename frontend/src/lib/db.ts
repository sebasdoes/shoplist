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
  /** Owning list (schema v2). Required for every push mutation. */
  list_id: string;
  /** feature-004 (schema v4): display order for the list's active queue,
   * lower renders first. Server-assigned sequential on reorder; new items
   * get min−1 (top insert). Rides the row — no structural IDB change; rows
   * without it (pre-v4 local values / older server rows) read as 0. */
  sort_order: number;
  // Local-only conveniences (never sent to the server):
  ticked: boolean;
  ticked_at: string | null;
  local_created_at: string; // client ordering only, not a conflict source
}

/** T006 — lists store (schema v2), mirroring the server `lists` table.
 * feature-003: rows gain `sort_order` (schema v3; household-wide display
 * order, lower renders first). No structural IndexedDB change is needed —
 * the field rides the row; missing (pre-v3 local) values read as 0. */
export interface List {
  id: string;
  name: string; // trimmed, ≤100 chars, NOT unique
  created_by: string | null;
  created_at: string;
  tombstone: boolean;
  rev: number;
  sort_order: number;
}

/** Fixed uuid of the migration-seeded default list (data-model.md). */
export const DEFAULT_LIST_ID = '00000000-0000-4000-8000-000000000001';

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
  /** Trips are scoped to ONE list; decisions resolve within it (FR-017). */
  list_id: string;
  decisions: TripDecision[];
}

/** Item payload shaped exactly for POST /sync/push `upsert_item` (contracts/api.md). */
export interface OutboxItemMutation {
  id: string;
  /** Owning list — REQUIRED by the v2 contract on every upsert_item. */
  list_id: string;
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
    }
  // T011 — list ops ride the SAME outbox (FR-022)
  | {
      op: 'create_list';
      list: { id: string; name: string };
      ts: string;
      seq?: number;
    }
  | {
      op: 'delete_list';
      list_id: string;
      ts: string;
      seq?: number;
    }
  // feature-003 — full household-wide ordering, one op (schema v3)
  | {
      op: 'reorder_lists';
      order: string[]; // every live list id, new sequence
      ts: string;
      seq?: number;
    }
  // feature-004 — reorder a list's ACTIVE unticked queue, one op (schema v4).
  // FLAT wire shape: { list_id, order: [item ids] } — order is a COMPLETE
  // unticked active set of that list; ticked rows keep their sort_order.
  | {
      op: 'reorder_items';
      list_id: string;
      order: string[];
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
  lists: {
    key: string;
    value: List;
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
const DB_VERSION = 2;

let dbp: Promise<IDBPDatabase<ShoplistDB>> | null = null;

export function initDb(): Promise<IDBPDatabase<ShoplistDB>> {
  if (!dbp) {
    dbp = openDB<ShoplistDB>(DB_NAME, DB_VERSION, {
      upgrade(db, oldVersion, _newVersion, tx) {
        if (oldVersion < 1) {
          const items = db.createObjectStore('items', { keyPath: 'id' });
          items.createIndex('by_rev', 'rev');
          items.createIndex('by_status', 'status');

          const history = db.createObjectStore('history', { keyPath: 'id' });
          history.createIndex('by_occurred_at', 'occurred_at');
          history.createIndex('by_rev', 'rev');

          db.createObjectStore('outbox', { keyPath: 'seq', autoIncrement: true });
          db.createObjectStore('state');
        }
        // T006 — v1→v2: add the `lists` store. Existing local DBs only hold
        // mirrored subsets (single-list server), so the simplest lossless
        // migration is to drop the mirrors and resync: clearing `state` resets
        // the watermark, forcing a full watermark-0 snapshot (lists included).
        db.createObjectStore('lists', { keyPath: 'id' });
        if (oldVersion >= 1) {
          tx.objectStore('items').clear();
          tx.objectStore('history').clear();
          tx.objectStore('outbox').clear();
          tx.objectStore('state').clear();
        }
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

// ---- lists (T006) ----

export async function getAllLists(): Promise<List[]> {
  return (await conn()).getAll('lists');
}

export async function getList(id: string): Promise<List | undefined> {
  return (await conn()).get('lists', id);
}

export async function putList(list: List): Promise<void> {
  const db = await conn();
  await db.put('lists', list);
}

export async function putLists(lists: List[]): Promise<void> {
  const db = await conn();
  const tx = db.transaction('lists', 'readwrite');
  for (const list of lists) tx.store.put(list);
  await tx.done;
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
  const tx = db.transaction(['items', 'history', 'lists', 'outbox', 'state'], 'readwrite');
  tx.objectStore('items').clear();
  tx.objectStore('history').clear();
  tx.objectStore('lists').clear();
  tx.objectStore('outbox').clear();
  tx.objectStore('state').clear();
  await tx.done;
}

export async function resetForTests(): Promise<void> {
  await deleteDatabaseForTests();
  await initDb();
}

/** Closes the module connection and deletes the whole DB (no re-open). */
export async function deleteDatabaseForTests(): Promise<void> {
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
}

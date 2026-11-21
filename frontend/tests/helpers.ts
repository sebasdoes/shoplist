import { get } from 'svelte/store';
import { vi } from 'vitest';
import * as db from '../src/lib/db';
import type { Item, List } from '../src/lib/db';
import {
  activeItems,
  addItem,
  setTick,
  deleteItem,
  saveItemEdit,
  toggleTick,
} from '../src/lib/stores';
import { configureApi } from '../src/lib/api';
import { clearPostMutationTimerForTests } from '../src/lib/sync';

type FetchFn = ReturnType<typeof vi.fn>;

function jsonResponse(body: object, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

/** Route by API path: /api/v1/sync/push, /api/v1/sync/pull, /api/v1/history. */
export function routeFetch(handlers: {
  push?: (body: unknown) => Response | Promise<Response>;
  pull?: (body: unknown) => Response | Promise<Response>;
  getHistory?: () => Response | Promise<Response>;
  deleteHistory?: (id: string) => Response | Promise<Response>;
}): FetchFn {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/sync/push')) {
      if (!handlers.push) throw new TypeError('network down (push unsupported)');
      return handlers.push(JSON.parse(String(init?.body ?? '{}')));
    }
    if (url.endsWith('/sync/pull')) {
      if (!handlers.pull) throw new TypeError('network down (pull unsupported)');
      return handlers.pull(JSON.parse(String(init?.body ?? '{}')));
    }
    if (url.endsWith('/history') && (init?.method ?? 'GET') !== 'DELETE') {
      if (!handlers.getHistory) return jsonResponse([]);
      return handlers.getHistory();
    }
    if (url.includes('/history/')) {
      const id = decodeURIComponent(url.split('/history/')[1] ?? '');
      if (!handlers.deleteHistory) throw new TypeError('network down (delete unsupported)');
      return handlers.deleteHistory(id);
    }
    throw new TypeError(`network down: ${url}`);
  }) as FetchFn;
}

export function seedItem(overrides: Partial<Item> = {}): Item {
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
    created_by: 'server',
    created_at: now,
    updated_at: now,
    rev: overrides.rev ?? 10,
    list_id: overrides.list_id ?? db.DEFAULT_LIST_ID,
    ticked: overrides.ticked ?? false,
    ticked_at: overrides.ticked_at ?? null,
    local_created_at: overrides.local_created_at ?? now,
    ...overrides,
  } as Item;
}

/** A live list row shaped as stored in the `lists` IDB store. */
export function seedList(overrides: Partial<List> = {}): List {
  const now = new Date().toISOString();
  return {
    id: overrides.id ?? crypto.randomUUID(),
    name: overrides.name ?? 'Boodschappen',
    created_by: overrides.created_by ?? 'server',
    created_at: overrides.created_at ?? now,
    tombstone: overrides.tombstone ?? false,
    rev: overrides.rev ?? 10,
  };
}

export function apiItemOf(item: Item) {
  return {
    id: item.id,
    name: item.name,
    quantity: item.quantity,
    note: item.note,
    status: item.status,
    staple: item.staple,
    tombstone: item.tombstone,
    created_by: item.created_by,
    rev: item.rev,
    list_id: item.list_id,
  };
}

export function configureTestApi() {
  configureApi({
    authHeaderProvider: async () => 'test-token',
    onUnauthorized: async () => false,
  });
}

export function activeNames(): string[] {
  return get(activeItems).map((i) => i.name);
}

export async function tickItem(item: Item) {
  await setTick(item, true);
}

export { clearPostMutationTimerForTests, addItem, toggleTick, deleteItem, saveItemEdit, db };

// T011 — typed fetch wrapper per contracts/api.md.
// Adds Authorization, throws typed errors, and on 401 invokes the re-auth hook
// (silent refresh, then a single retry) before failing.
import { config } from './config';

export interface ApiItem {
  id: string;
  name: string;
  quantity: string | null;
  note: string | null;
  status: 'active' | 'completed' | 'dropped';
  staple: boolean;
  tombstone: boolean;
  created_by: string;
  rev: number;
  /** Owning list (schema v2). Present in pull responses from v2 servers. */
  list_id?: string;
  /** feature-004 (schema v4): display rank for the active queue. Absent on
   * pre-v4 servers → the caller keeps its local value. */
  sort_order?: number;
}

export interface AppliedEntry {
  id: string;
  rev: number;
  updated_at: string;
  merged_into: string | null;
  /** feature-004: the reorder echo carries the server-assigned index. */
  sort_order?: number;
}

export interface AppliedGroup {
  items: AppliedEntry[];
  lists: AppliedList[];
}

export interface PushTripResponse {
  id: string;
  duplicate: boolean;
  readded: { id: string; name: string }[];
}

export interface PushItemMutation {
  id: string;
  /** Owning list — required by the v2 contract on every upsert_item. */
  list_id: string;
  name?: string;
  quantity?: string | null;
  note?: string | null;
  staple?: boolean;
  tombstone?: boolean;
}

export interface PushMutation {
  op:
    | 'upsert_item'
    | 'trip'
    | 'create_list'
    | 'delete_list'
    | 'reorder_lists'
    | 'reorder_items';
  item?: PushItemMutation;
  trip?: {
    id: string;
    list_id: string;
    decisions: { item_id: string; outcome: 'bought' | 'kept' | 'not_bought' }[];
  };
  list?: { id: string; name: string };
  list_id?: string;
  /** reorder_lists / reorder_items: the new sequence as a flat id array. */
  order?: string[];
}

/** A list echoed back by the server in the push response's `lists` section.
 * feature-003: v3 servers also echo `sort_order`. */
export interface AppliedList {
  id: string;
  name: string;
  rev: number;
  tombstone: boolean;
  sort_order?: number;
}

/** A list row in the pull response (contracts/api.md). feature-003: v3 rows
 * carry `sort_order`. */
export interface ApiList {
  id: string;
  name: string;
  tombstone: boolean;
  rev: number;
  sort_order?: number;
}

export interface PushRequest {
  mutations: PushMutation[];
}

export interface PushResponse {
  applied: AppliedGroup;
  trip?: PushTripResponse;
  /** feature-004 poison fix: entries the server could not apply, by index. */
  rejected?: { index: number; op: string; detail: string }[];
}

export interface PullRequest {
  watermark: number;
  include_history: boolean;
}

export interface ApiHistoryEntry {
  id: string;
  name: string;
  quantity: string | null;
  note: string | null;
  outcome: 'bought' | 'not_bought';
  actor: string;
  occurred_at: string;
  rev: number;
}

export interface PullResponse {
  watermark: number;
  lists?: ApiList[];
  items: ApiItem[];
  history: ApiHistoryEntry[];
}

export class ApiError extends Error {
  constructor(
    public kind: 'network' | 'http' | 'unauthorized',
    message: string,
    public status?: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export type AuthHeaderProvider = () => Promise<string | null>;
export type ReAuthFn = () => Promise<boolean>;

let authHeaderProvider: AuthHeaderProvider = async () => null;
let reAuthFn: ReAuthFn = async () => false;

export function configureApi(opts: {
  authHeaderProvider?: AuthHeaderProvider;
  onUnauthorized?: ReAuthFn;
}): void {
  if (opts.authHeaderProvider) authHeaderProvider = opts.authHeaderProvider;
  if (opts.onUnauthorized) reAuthFn = opts.onUnauthorized;
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
  retried = false,
): Promise<T> {
  const token = await authHeaderProvider();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init.headers as Record<string, string> | undefined),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(`${config.apiBase}${path}`, { ...init, headers });
  } catch {
    throw new ApiError('network', 'Network unavailable');
  }

  if (res.status === 401 && !retried) {
    const reauthed = await reAuthFn();
    if (reauthed) return apiRequest<T>(path, init, true);
  }
  if (res.status === 401) throw new ApiError('unauthorized', 'Unauthorized', 401);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      /* body not json */
    }
    throw new ApiError('http', detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const apiPush = (body: PushRequest): Promise<PushResponse> =>
  apiRequest<PushResponse>('/sync/push', { method: 'POST', body: JSON.stringify(body) });

export const apiPull = (body: PullRequest): Promise<PullResponse> =>
  apiRequest<PullResponse>('/sync/pull', { method: 'POST', body: JSON.stringify(body) });

export const apiGetHistory = (): Promise<ApiHistoryEntry[]> =>
  apiRequest<ApiHistoryEntry[]>('/history');

export const apiDeleteHistory = (id: string): Promise<void> =>
  apiRequest<void>(`/history/${encodeURIComponent(id)}`, { method: 'DELETE' });

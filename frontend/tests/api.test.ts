import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiRequest, configureApi, ApiError } from '../src/lib/api';

function jsonResponse(body: object, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const providers = {
  authHeaderProvider: (token: string | null) => async () => token,
  onUnauthorized: (reauthed: boolean) => async () => reauthed,
};

beforeEach(() => {
  configureApi({
    authHeaderProvider: providers.authHeaderProvider('tok-1'),
    onUnauthorized: providers.onUnauthorized(false),
  });
});

describe('api wrapper (T011)', () => {
  it('injects Bearer token and parses typed JSON', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ watermark: 5, items: [], history: [] }));
    vi.stubGlobal('fetch', fetchMock);

    const res = await apiRequest<{ watermark: number }>('/sync/pull', {
      method: 'POST',
      body: '{}',
    });
    expect(res.watermark).toBe(5);
    const headers = (fetchMock as ReturnType<typeof vi.fn>).mock.calls[0][1].headers;
    expect(headers['Authorization']).toBe('Bearer tok-1');
    expect(headers['Content-Type']).toBe('application/json');
  });

  it('maps network failure to ApiError(network)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('offline');
      }),
    );
    await expect(apiRequest('/x')).rejects.toBeInstanceOf(ApiError);
    await expect(apiRequest('/x')).rejects.toMatchObject({ kind: 'network' });
  });

  it('on 401 invokes the re-auth hook and retries once with a new token', async () => {
    let call = 0;
    let reauthCalls = 0;
    configureApi({
      authHeaderProvider: async () => (call++ === 0 ? 'expired' : 'fresh'),
      onUnauthorized: async () => {
        reauthCalls++;
        return true;
      },
    });
    const fetchMock = vi.fn(async () =>
      call <= 1 ? jsonResponse({ detail: 'expired' }, 401) : jsonResponse({ ok: true }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiRequest('/sync/push', { method: 'POST' })).resolves.toEqual({ ok: true });
    expect(reauthCalls).toBe(1);
  });

  it('after a failed re-auth attempt surfaces ApiError(unauthorized)', async () => {
    configureApi({
      authHeaderProvider: async () => 'expired',
      onUnauthorized: async () => false,
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: 'nope' }, 401)),
    );
    await expect(apiRequest('/x')).rejects.toMatchObject({ kind: 'unauthorized' });
  });

  it('reads server error detail ({detail}) for 4xx responses', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: 'name bad' }, 422)),
    );
    await expect(apiRequest('/x')).rejects.toMatchObject({
      kind: 'http',
      message: 'name bad',
      status: 422,
    });
  });

  it('handles 204 with no body (DELETE /history/{id})', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(null, { status: 204 })),
    );
    await expect(apiRequest('/history/h1', { method: 'DELETE' })).resolves.toBeUndefined();
  });
});

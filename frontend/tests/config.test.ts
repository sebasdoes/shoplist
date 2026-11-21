// Runtime OIDC config: the frontend must adopt the server-deployed issuer and
// audience (build-time values are placeholders, the public repo carries none).
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { config, loadRuntimeConfig } from '../src/lib/config';

function stubConfigEndpoint(issuer: string | undefined, audience: string | undefined, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ issuer, audience }), { status }),
    ),
  );
}

describe('loadRuntimeConfig (deploy-time OIDC values)', () => {
  beforeEach(() => {
    config.oidcIssuer = 'https://sso.example.com';
    config.oidcClientId = '00000000-0000-0000-0000-000000000000';
  });

  it('adopts the server-provided issuer and audience', async () => {
    stubConfigEndpoint('https://real-sso.example', 'abc-not-a-real-uuid');
    await loadRuntimeConfig();
    expect(fetch).toHaveBeenCalledWith('/api/v1/config');
    expect(config.oidcIssuer).toBe('https://real-sso.example');
    expect(config.oidcClientId).toBe('abc-not-a-real-uuid');
  });

  it('keeps build defaults when the endpoint is unreachable (offline)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline'); }));
    await loadRuntimeConfig();
    expect(config.oidcIssuer).toBe('https://sso.example.com');
  });
});

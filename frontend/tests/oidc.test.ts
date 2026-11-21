import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as db from '../src/lib/db';
import * as oidc from '../src/lib/oidc';
import { config } from '../src/lib/config';

const VERIFIER_RFC7636 = 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk';
const CHALLENGE_RFC7636 = 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM';

function tokenEndpoint(res: object, status = 200) {
  return new Response(JSON.stringify(res), { status });
}

function idToken(claims: Record<string, unknown>): string {
  const head = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).replace(/=+$/, '');
  const body = btoa(JSON.stringify(claims)).replace(/=+$/, '');
  return `${head}.${body}.sig`;
}

beforeEach(async () => {
  oidc.resetForTests();
});

describe('oidc (T009)', () => {
  it('derives the S256 challenge per RFC 7636 test vector', async () => {
    await expect(oidc.pkceChallengeS256(VERIFIER_RFC7636)).resolves.toBe(CHALLENGE_RFC7636);
  });

  it('generates verifiers in the 43..128 char range', () => {
    const v = oidc.randomCodeVerifier();
    expect(v.length).toBeGreaterThanOrEqual(43);
    expect(v.length).toBeLessThanOrEqual(128);
    expect(v).toMatch(/^[A-Za-z0-9_-]+$/);
  });

  it('builds an authorize URL with PKCE S256 + state, required scopes', async () => {
    const url = oidc.buildAuthorizeUrl(oidc.defaultEndpoints(), CHALLENGE_RFC7636, 'teststate123');
    const u = new URL(url);
    expect(u.origin + u.pathname).toBe(config.oidcIssuer + '/authorize');
    expect(u.searchParams.get('client_id')).toBe(config.oidcClientId);
    expect(u.searchParams.get('response_type')).toBe('code');
    expect(u.searchParams.get('code_challenge_method')).toBe('S256');
    expect(u.searchParams.get('code_challenge')).toBe(CHALLENGE_RFC7636);
    expect(u.searchParams.get('scope')).toBe('openid profile email offline_access');
    expect(u.searchParams.get('state')).toBe('teststate123');
  });

  it('randomState is long enough for Pocket ID (>= 8 chars)', () => {
    const s = oidc.randomState();
    expect(s.length).toBeGreaterThanOrEqual(8);
    expect(s).toMatch(/^[A-Za-z0-9_-]+$/);
  });

  it('startLogin persists a state that lands in the authorize URL (regression: empty state was rejected)', async () => {
    await db.setState('oidc_endpoints', oidc.defaultEndpoints());
    const assigned: string[] = [];
    const assign = vi.fn((u: string) => assigned.push(u));
    vi.stubGlobal('location', { assign, search: '', pathname: '/' });
    vi.stubGlobal('history', { replaceState: vi.fn() });
    await oidc.startLogin();
    expect(assigned).toHaveLength(1);
    const u = new URL(assigned[0]);
    const persisted = await db.getState<string>('oidc_state');
    expect(persisted).toBeTruthy();
    expect(u.searchParams.get('state')).toBe(persisted);
    expect((persisted as string).length).toBeGreaterThanOrEqual(8);
  });

  it('exchanges the code, caches the session in IndexedDB, and exposes the access token', async () => {
    await db.setState('oidc_endpoints', oidc.defaultEndpoints()); // skip discovery fetch in test
    const fetchMock = vi.fn(async (_url: unknown) =>
      tokenEndpoint({
        access_token: 'at-1',
        refresh_token: 'rt-1',
        id_token: idToken({ sub: 'user-a', name: 'User A', exp: 9 }),
        expires_in: 3600,
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const session = await oidc.completeLoginWithCode('abc', VERIFIER_RFC7636);
    expect(session.access_token).toBe('at-1');
    expect(session.sub).toBe('user-a');
    expect(session.name).toBe('User'); // T022: display name from token claims, FIRST name only
    expect(session.exp).toBeGreaterThan(Date.now() / 1000);

    // persisted in the db state store
    expect(await db.getState<oidc.Session>('session')).toMatchObject({ access_token: 'at-1' });
    expect(await db.getState('pkce_code_verifier')).toBeNull();

    const tokenCall = fetchMock.mock.calls.find(
      (c) =>
        ((c as unknown[])[1] as { body?: string } | undefined)?.body?.includes(
          'authorization_code',
        ) ?? false,
    ) as unknown as [unknown, { body: string }];
    expect(tokenCall).toBeDefined();
    const grant = new URLSearchParams(tokenCall[1].body);
    expect(grant.get('grant_type')).toBe('authorization_code');
    expect(grant.get('code_verifier')).toBe(VERIFIER_RFC7636);

    await expect(oidc.ensureFreshToken()).resolves.toBe('at-1');
  });

  it('silently refreshes via refresh_token when the access token is stale (T009 silent refresh)', async () => {
    await db.setState('oidc_endpoints', oidc.defaultEndpoints()); // skip discovery fetch in test
    await db.setState('session', {
      access_token: 'stale',
      refresh_token: 'rt-1',
      id_token: null,
      exp: Date.now() / 1000 - 100, // expired
      sub: 'user-a',
      name: 'User A',
    });

    const fetchMock = vi.fn(async () =>
      tokenEndpoint({
        access_token: 'fresh',
        expires_in: 3600,
        id_token: idToken({ sub: 'user-a', name: 'User A' }),
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(oidc.ensureFreshToken()).resolves.toBe('fresh');
    const tokenCall = fetchMock.mock.calls.find(
      (c) =>
        ((c as unknown[])[1] as { body?: string } | undefined)?.body?.includes(
          'grant_type=refresh_token',
        ) ?? false,
    ) as unknown as [unknown, { body: string }];
    expect(tokenCall).toBeDefined();
    const grant = new URLSearchParams(tokenCall[1].body);
    expect(grant.get('grant_type')).toBe('refresh_token');
    expect(grant.get('refresh_token')).toBe('rt-1');
  });

  it('returns null (no interactive redirect) when refresh fails offline', async () => {
    await db.setState('session', {
      access_token: 'stale',
      refresh_token: 'rt-1',
      id_token: null,
      exp: Date.now() / 1000 - 100,
      sub: 'user-a',
      name: 'S',
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('network down');
      }),
    );
    // ensureFreshToken must not redirect; returns null → app stays usable offline
    await expect(oidc.ensureFreshToken()).resolves.toBeNull();
  });

  it('decodes JWT payloads for claims', () => {
    const jwt = `${btoa('{"alg":"none"}').replace(/=+$/, '')}.${btoa(
      JSON.stringify({ sub: 'u1', name: 'User B' }),
    ).replace(/=+$/, '')}.x`;
    expect(oidc.decodeJwtPayload(jwt)).toEqual({ sub: 'u1', name: 'User B' });
  });

  it('signOut clears the session', async () => {
    await db.setState('session', { access_token: 'tok', exp: 9e12, sub: 's', name: 'N' });
    oidc.resetForTests();
    await oidc.signOut();
    await expect(oidc.getSession()).resolves.toBeNull();
  });
});

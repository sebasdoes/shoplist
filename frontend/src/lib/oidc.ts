// T009 — OIDC public client with PKCE (S256) against Pocket ID.
// Token cache lives in IndexedDB (state store). 401 -> silent refresh via
// refresh_token, else interactive redirect. Session claims also feed T022
// (display name).
import * as db from './db';
import { config, loadRuntimeConfig } from './config';

const SESSION_KEY = 'session';
const VERIFIER_KEY = 'pkce_code_verifier';
const ENDPOINTS_KEY = 'oidc_endpoints';

export interface Session {
  access_token: string;
  refresh_token: string | null;
  id_token: string | null;
  /** client-clock expiry for proactive refresh only; the server re-validates every JWT (D7) */
  exp: number;
  sub: string;
  name: string;
}

interface TokenResponse {
  access_token: string;
  refresh_token?: string;
  id_token?: string;
  expires_in?: number;
}

interface Endpoints {
  authorization_endpoint: string;
  token_endpoint: string;
}

let cached: Session | null | undefined;

export function defaultEndpoints(): Endpoints {
  return {
    authorization_endpoint: `${config.oidcIssuer}/authorize`,
    token_endpoint: `${config.oidcIssuer}/api/oidc/token`,
  };
}

let endpointsCache: Endpoints | null = null;

export async function getEndpoints(): Promise<Endpoints> {
  await loadRuntimeConfig();
  if (endpointsCache) return endpointsCache;
  const fallback = defaultEndpoints();
  try {
    const res = await fetch(`${config.oidcIssuer}/.well-known/openid-configuration`);
    if (res.ok) {
      const disc = (await res.json()) as Partial<Endpoints>;
      if (disc.authorization_endpoint && disc.token_endpoint) {
        endpointsCache = {
          authorization_endpoint: disc.authorization_endpoint,
          token_endpoint: disc.token_endpoint,
        };
        await db.setState(ENDPOINTS_KEY, endpointsCache);
        return endpointsCache;
      }
    }
  } catch {
    /* offline / discovery unavailable */
  }
  if (!endpointsCache) {
    endpointsCache = (await db.getState<Endpoints>(ENDPOINTS_KEY)) ?? null;
  }
  return endpointsCache ?? fallback;
}

// ---- PKCE primitives ----

export function base64UrlEncodeBytes(bytes: Uint8Array): string {
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  const b64 = btoa(bin);
  return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function randomCodeVerifier(): string {
  const bytes = new Uint8Array(48); // -> 64 base64url chars (allowed: 43..128)
  crypto.getRandomValues(bytes);
  return base64UrlEncodeBytes(bytes);
}

/** OIDC state: required by Pocket ID (>= 8 chars); doubles as CSRF guard. */
export function randomState(): string {
  const bytes = new Uint8Array(24); // 32 base64url chars
  crypto.getRandomValues(bytes);
  return base64UrlEncodeBytes(bytes);
}

export async function pkceChallengeS256(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return base64UrlEncodeBytes(new Uint8Array(digest));
}

export function decodeBase64UrlToString(s: string): string {
  const b64 = s.replace(/-/g, '+').replace(/_/g, '/');
  const pad = b64.length % 4 === 0 ? '' : '='.repeat(4 - (b64.length % 4));
  return atob(b64 + pad);
}

export function decodeJwtPayload(jwt: string): Record<string, unknown> {
  const parts = jwt.split('.');
  if (parts.length < 2) throw new Error('not a JWT');
  return JSON.parse(decodeBase64UrlToString(parts[1])) as Record<string, unknown>;
}

// ---- flow ----

export function buildAuthorizeUrl(
  endpoints: Endpoints,
  challenge: string,
  state: string,
  redirectUri = config.redirectUri,
  clientId = config.oidcClientId,
  scopes = config.oidcScopes,
): string {
  const url = new URL(endpoints.authorization_endpoint);
  url.searchParams.set('response_type', 'code');
  url.searchParams.set('client_id', clientId);
  url.searchParams.set('redirect_uri', redirectUri);
  url.searchParams.set('scope', scopes);
  url.searchParams.set('state', state);
  url.searchParams.set('code_challenge', challenge);
  url.searchParams.set('code_challenge_method', 'S256');
  return url.toString();
}

const STATE_KEY = 'oidc_state';

export async function startLogin(): Promise<void> {
  const verifier = randomCodeVerifier();
  const state = randomState();
  await db.setState(VERIFIER_KEY, verifier);
  await db.setState(STATE_KEY, state);
  const challenge = await pkceChallengeS256(verifier);
  const endpoints = await getEndpoints();
  const url = buildAuthorizeUrl(endpoints, challenge, state);
  if (typeof location !== 'undefined') {
    location.assign(url);
  }
}

async function tokenRequest(body: URLSearchParams): Promise<TokenResponse> {
  await loadRuntimeConfig();
  const endpoints = await getEndpoints();
  const res = await fetch(endpoints.token_endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });
  if (!res.ok) throw new Error(`token endpoint returned ${res.status}`);
  return (await res.json()) as TokenResponse;
}

function extractClaims(t: TokenResponse): Record<string, unknown> {
  let claims: Record<string, unknown> = {};
  try {
    if (t.id_token) claims = decodeJwtPayload(t.id_token);
  } catch {
    claims = {};
  }
  if (!claims.sub) {
    try {
      claims = decodeJwtPayload(t.access_token);
    } catch {
      claims = {};
    }
  }
  return claims;
}

async function saveTokens(t: TokenResponse): Promise<Session> {
  const claims = extractClaims(t);
  const sub = String(claims.sub ?? '');
  const name =
    (typeof claims.name === 'string' && claims.name) ||
    (typeof claims.preferred_username === 'string' && claims.preferred_username) ||
    sub ||
    'Household member';
  // Client clock is only used to decide when to *proactively* refresh;
  // ordering/conflicts remain server-stamped (constitution IV).
  const exp =
    t.expires_in !== undefined
      ? Date.now() / 1000 + t.expires_in
      : typeof claims.exp === 'number'
        ? claims.exp
        : Date.now() / 1000 + 3600;
  const session: Session = {
    access_token: t.access_token,
    refresh_token: t.refresh_token ?? null,
    id_token: t.id_token ?? null,
    exp,
    sub,
    name,
  };
  cached = session;
  await db.setState(SESSION_KEY, session);
  return session;
}

/** Exchange an authorization code (called with params from the redirect). */
export async function completeLoginWithCode(code: string, verifier: string): Promise<Session> {
  const token = await tokenRequest(
    new URLSearchParams({
      grant_type: 'authorization_code',
      code,
      client_id: config.oidcClientId,
      redirect_uri: config.redirectUri,
      code_verifier: verifier,
    }),
  );
  await db.setState(VERIFIER_KEY, null);
  return saveTokens(token);
}

/** Detects ?code= on the callback URL and completes the flow. Returns true on login.
 * Verifies the OIDC state parameter (CSRF guard); surfaces provider errors. */
export async function handleRedirectIfPresent(): Promise<boolean> {
  if (typeof location === 'undefined') return false;
  const params = new URLSearchParams(location.search);
  const oidcError = params.get('error');
  if (oidcError) {
    const msg = `oidc_error ${oidcError}: ${params.get('error_description') ?? ''}`;
    console.error(msg);
    void import('./log').then((m) => m.logBreadcrumb(msg));
    if (typeof history !== 'undefined') {
      history.replaceState(null, '', location.pathname);
    }
    return false;
  }
  const code = params.get('code');
  if (!code) return false;
  const expected = await db.getState<string>(STATE_KEY);
  const returned = params.get('state');
  if (!expected || !returned || returned !== expected) {
    void import('./log').then((m) =>
      m.logBreadcrumb(`state-mismatch returned=${returned ?? 'none'}`),
    );
    if (typeof history !== 'undefined') {
      history.replaceState(null, '', location.pathname);
    }
    return false;
  }
  const verifier = await db.getState<string>(VERIFIER_KEY);
  if (!verifier) return false;
  await completeLoginWithCode(code, verifier);
  await db.setState(STATE_KEY, null);
  if (typeof history !== 'undefined') {
    history.replaceState(null, '', location.pathname);
  }
  return true;
}

export async function getSession(): Promise<Session | null> {
  if (cached !== undefined) return cached;
  cached = (await db.getState<Session>(SESSION_KEY)) ?? null;
  return cached;
}

/** Silent refresh via refresh_token grant. Returns null on any failure. */
export async function refreshSession(): Promise<Session | null> {
  const s = await getSession();
  if (!s?.refresh_token) return null;
  try {
    const t = await tokenRequest(
      new URLSearchParams({
        grant_type: 'refresh_token',
        refresh_token: s.refresh_token,
        client_id: config.oidcClientId,
      }),
    );
    return await saveTokens({ ...t, refresh_token: t.refresh_token ?? s.refresh_token });
  } catch {
    return null;
  }
}

/**
 * Returns a valid access token, or null when interactive sign-in is needed.
 * Never triggers an interactive redirect (does not block offline usage).
 */
export async function ensureFreshToken(): Promise<string | null> {
  let s = await getSession();
  if (!s) {
    if (await handleRedirectIfPresent()) s = await getSession();
  }
  if (!s) return null;
  if (s.exp > Date.now() / 1000 + 30) return s.access_token; // 30s skew guard
  s = await refreshSession();
  return s ? s.access_token : null;
}

/** Re-auth hook for api.ts: silent first, interactive redirect as last resort. */
export async function reAuthenticate(): Promise<boolean> {
  const s = await refreshSession();
  if (s) return true;
  await startLogin();
  return false;
}

export async function signOut(): Promise<void> {
  cached = null;
  await db.setState(SESSION_KEY, null);
}

/** Clear in-memory mirror so a fresh state store is picked up (tests). */
export function resetForTests(): void {
  cached = undefined;
  endpointsCache = null;
}

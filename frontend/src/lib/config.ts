// Central configuration. Dev values per tasks.md T003/T009; overridable via
// Vite env vars so production builds can point at the real origin.
const env = import.meta.env;

function origin(): string {
  return typeof location !== 'undefined' ? location.origin : 'http://localhost:5173';
}

export const config = {
  apiBase: (env.VITE_API_BASE as string | undefined) ?? '/api/v1',
  oidcIssuer: (env.VITE_OIDC_ISSUER as string | undefined) ?? 'https://sso.example.com',
  oidcClientId:
    (env.VITE_OIDC_CLIENT_ID as string | undefined) ?? 'abac6ecf-d312-4ed1-a3f8-46e5958a0fe8',
  oidcScopes: (env.VITE_OIDC_SCOPES as string | undefined) ?? 'openid profile email offline_access',
  // Dev callback per task context; production uses the app origin.
  redirectUri: (env.VITE_OIDC_REDIRECT_URI as string | undefined) ?? `${origin()}/`,
};

let runtimeLoaded = false;

/**
 * Pull deploy-time OIDC values from the sync server's unauthenticated
 * /config endpoint. Runs once before any auth flow; in a deployed image the
 * build-time defaults are placeholders, the real issuer/audience come from here.
 * Offline/failed fetch = build-time defaults stay until the next online pass.
 */
export async function loadRuntimeConfig(): Promise<void> {
  if (runtimeLoaded) return;
  runtimeLoaded = true;
  try {
    const res = await fetch(`${config.apiBase}/config`);
    if (!res.ok) return;
    const d = (await res.json()) as { issuer?: string; audience?: string };
    if (typeof d.issuer === 'string' && d.issuer) config.oidcIssuer = d.issuer;
    if (typeof d.audience === 'string' && d.audience) config.oidcClientId = d.audience;
  } catch {
    /* offline or not yet deployed: defaults are harmless placeholders */
  }
}

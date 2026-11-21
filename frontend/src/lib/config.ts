// Central configuration. Dev values per tasks.md T003/T009; overridable via
// Vite env vars so production builds can point at the real origin.
const env = import.meta.env;

function origin(): string {
  return typeof location !== 'undefined' ? location.origin : 'http://localhost:5173';
}

export const config = {
  apiBase: (env.VITE_API_BASE as string | undefined) ?? '/api/v1',
  oidcIssuer: (env.VITE_OIDC_ISSUER as string | undefined) ?? 'https://id.akiha.nl',
  oidcClientId:
    (env.VITE_OIDC_CLIENT_ID as string | undefined) ?? 'abac6ecf-d312-4ed1-a3f8-46e5958a0fe8',
  oidcScopes: (env.VITE_OIDC_SCOPES as string | undefined) ?? 'openid profile email offline_access',
  // Dev callback per task context; production uses the app origin.
  redirectUri: (env.VITE_OIDC_REDIRECT_URI as string | undefined) ?? `${origin()}/`,
} as const;

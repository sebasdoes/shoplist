// Wires api.ts auth providers to oidc.ts without a module cycle, and lets
// tests inject canned providers (the server is not running locally).
import { configureApi } from './api';
import * as oidc from './oidc';

export function bootstrapServices(overrides?: {
  authHeaderProvider?: () => Promise<string | null>;
  onUnauthorized?: () => Promise<boolean>;
}): void {
  configureApi({
    authHeaderProvider: overrides?.authHeaderProvider ?? (async () => oidc.ensureFreshToken()),
    onUnauthorized: overrides?.onUnauthorized ?? (async () => oidc.reAuthenticate()),
  });
}

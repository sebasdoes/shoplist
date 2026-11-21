// Diagnostic beacon: POST browser-side errors to the server log so a silent
// OIDC failure can be seen from docker logs (unauthenticated, stores nothing).
export async function logBreadcrumb(msg: string): Promise<void> {
  try {
    await fetch('/api/v1/log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ msg }),
    });
  } catch {
    /* the beacon must never become the error */
  }
}

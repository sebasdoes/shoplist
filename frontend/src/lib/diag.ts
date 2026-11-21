// Build identity + boot breadcrumbs. Real sha is injected by CI
// (VITE_BUILD_SHA build arg); local dev gets a timestamp.
declare const __BUILD_SHA__: string;

export function buildId(): string {
  return __BUILD_SHA__;
}

/** Fire-and-forget breadcrumb: immediately POSTed so a freeze still shows the
 * last step that completed in docker logs. One small POST per boot step. */
export function bootStep(step: string): void {
  void (async () => {
    try {
      await fetch('/api/v1/log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ msg: `boot ${buildId().slice(0, 7)} ${step}` }),
      });
    } catch {
      /* the beacon must never become the error */
    }
  })();
}

export function bootErr(step: string, detail: string): void {
  bootStep(`${step} FAILED ${detail}`);
}

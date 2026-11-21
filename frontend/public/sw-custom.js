// Runs inside the precached Workbox service worker (imported via
// workbox.importScripts). Per contracts/api.md the SW never caches API
// responses — background sync just pokes the app to flush its IndexedDB
// outbox when the browser fires the `sync` event (reconnect, wifi regained).

self.addEventListener('sync', (event) => {
  if (event.tag !== 'shoplist-sync') return;
  event.waitUntil(
    self.clients
      .matchAll({ includeUncontrolled: true, type: 'window' })
      .then((clients) => {
        for (const client of clients) {
          client.postMessage({ type: 'SHOPLIST_SYNC_TRIGGER' });
        }
      })
      .catch(() => {
        /* best effort — the app also syncs on `online`/focus */
      }),
  );
});

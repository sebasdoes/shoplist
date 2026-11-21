import { mount } from 'svelte';
import './app.css';
import App from './App.svelte';

// FR-013: precached app shell, updated in the background WITHOUT reloading
// the live page. The default virtual:pwa-register hard-reloads on
// controllerchange — which interrupted any in-flight UI action (e.g. the
// hamburger menu) whenever a new bundle had been deployed. Instead:
//   - a new service worker activates (skipWaiting + clientsClaim in the
//     generated sw.js) and serves the NEXT full navigation,
//   - the currently-running page continues on its loaded bundle
//     (server sync contract is stable across versions),
//   - we nudge for updates periodically.
if ('serviceWorker' in navigator) {
  const register = navigator.serviceWorker
    .register('/sw.js')
    .then((reg) => {
      reg.addEventListener('updatefound', () => {
        const incoming = reg.installing;
        incoming?.addEventListener('statechange', () => {
          if (incoming.state === 'installed' && navigator.serviceWorker.controller) {
            incoming.postMessage({ type: 'SKIP_WAITING' });
          }
        });
      });
      window.setInterval(() => void reg.update().catch(() => {}), 30 * 60 * 1000);
      return reg;
    })
    .catch(() => null);
  void register;
}

const app = mount(App, {
  target: document.getElementById('app')!,
});

export default app;

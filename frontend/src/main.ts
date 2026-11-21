import { mount } from 'svelte';
import { registerSW } from 'virtual:pwa-register';
import './app.css';
import App from './App.svelte';

// PWA: precached app shell registers and updates silently (FR-013).
registerSW({ immediate: true });

const app = mount(App, {
  target: document.getElementById('app')!,
});

export default app;

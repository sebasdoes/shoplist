import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig({
  define: {
    __BUILD_SHA__: JSON.stringify(
      process.env.VITE_BUILD_SHA || `local-${new Date().toISOString().slice(0, 16)}`,
    ),
  },
  plugins: [
    svelte(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'sw-custom.js'],
      manifest: {
        name: 'Shoplist',
        short_name: 'Shoplist',
        description: 'One shared offline-first grocery list that syncs.',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        background_color: '#f6f4ee',
        theme_color: '#2f6f4f',
        icons: [
          { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
          {
            src: 'maskable-icon-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        // App shell precache only. Per contracts/api.md: NO caching of API
        // responses — IndexedDB is the cache.
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        navigateFallback: 'index.html',
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [],
        importScripts: ['sw-custom.js'],
        // New worker takes over at the next load instead of waiting for all
        // tabs to close — kills the "phone runs yesterday's bundle" class.
        skipWaiting: true,
        clientsClaim: true,
      },
    }),
  ],
  test: {
    environment: 'node',
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.ts'],
  },
});

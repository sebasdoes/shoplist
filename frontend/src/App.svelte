<script lang="ts">
  import { onMount } from 'svelte';
  import type { Item, TripDecision } from './lib/db';
  import {
    activeItems,
    activeList, // feature-003 (T008): header shows the ACTIVE list name
    addItem,
    userStore,
    toggleTick,
    deleteItem,
    saveItemEdit,
    bumpQuantity,
    completeTrip,
    loadStoresFromDb,
    wireStoresToSync,
  } from './lib/stores';
  import { initSyncTriggers, onSyncComplete, runSync } from './lib/sync';
  import { initDb } from './lib/db';
  import { bootErr, bootStep, buildId } from './lib/diag';
  import { bootstrapServices } from './lib/bootstrap';
  import * as oidc from './lib/oidc';
  import AddBar from './components/AddBar.svelte';
  import ItemRow from './components/ItemRow.svelte';
  import ItemEditSheet from './components/ItemEditSheet.svelte';
  import TripDoneSheet from './components/TripDoneSheet.svelte';
  import HistoryDrawer from './components/HistoryDrawer.svelte';
  import ListMenu from './components/ListMenu.svelte';

  type AuthState = 'checking' | 'signedOut' | 'signedIn';

  let auth: AuthState = $state('checking');
  let signInError: string | null = $state(null);
  let editing: Item | null = $state(null);
  let tripSheetOpen = $state(false);
  let historyOpen = $state(false);
  let menuOpen = $state(false);
  let status = $state<'idle' | 'syncing' | 'offline' | 'error'>('idle');

  const untickedItems = $derived($activeItems.filter((i) => !i.ticked));
  const tickedCount = $derived($activeItems.filter((i) => i.ticked).length);

  onMount(async () => {
    bootStep('onmount');
    onSyncComplete(() => {
      void loadStoresFromDb();
    });
    wireStoresToSync();
    await initDb();
    bootStep('db-opened');
    bootstrapServices();
    try {
      await oidc.handleRedirectIfPresent();
      bootStep('redirect-handled');
      const token = await oidc.ensureFreshToken(); // silent auth (T012)
      const session = await oidc.getSession();
      bootStep('auth-resolved');
      if (session) {
        userStore.set({ sub: session.sub, name: session.name }); // T022
        auth = 'signedIn';
        await loadStoresFromDb(); // render from IndexedDB first, network-independent
        initSyncTriggers();
        if (token !== null) void runSync('startup');
      } else {
        auth = 'signedOut';
      }
    } catch (e) {
      // Never freeze on "Shoplist…": surface the failure and fall back to
      // whatever IndexedDB already holds (a successful earlier exchange).
      const msg = e instanceof Error ? `${e.name}: ${e.message}` : String(e);
      bootErr('startup', msg);
      const fallback = await oidc.getSession().catch(() => null);
      if (fallback) {
        userStore.set({ sub: fallback.sub, name: fallback.name });
        auth = 'signedIn';
        await loadStoresFromDb();
        initSyncTriggers();
        void runSync('startup');
      } else {
        auth = 'signedOut';
        signInError = `Startup error: ${msg}`;
      }
    }
  });

  async function doSignIn(): Promise<void> {
    signInError = null;
    try {
      await oidc.startLogin();
    } catch (e) {
      const msg = e instanceof Error ? `${e.name}: ${e.message}` : String(e);
      signInError = `Sign-in failed: ${msg}`;
      void logBreadcrumb(`signin-failed ${msg}`);
    }
  }

  function chipLabel(s: 'idle' | 'syncing' | 'offline' | 'error'): string | null {
    if (s === 'syncing') return 'syncing…';
    if (s === 'error') return 'syncing…'; // quiet; keeps retrying
    if (s === 'offline') return 'offline — will sync';
    return null;
  }

  function confirmTrip(decisions: TripDecision[]) {
    if (decisions.length > 0) void completeTrip(decisions);
    tripSheetOpen = false;
  }

  function signOut() {
    void oidc.signOut().then(() => {
      auth = 'signedOut';
      userStore.set(null);
    });
  }
</script>

{#if auth === 'checking'}
  <div class="center-screen">
    <p>Shoplist…</p>
    <p class="build-id">{buildId().slice(0, 7)}</p>
  </div>
{:else if auth === 'signedOut'}
  <div class="center-screen">
    <span class="brand-logo" aria-label="Shoplist">
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.8"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path d="M5.5 9h13l-1.2 8.6a2.8 2.8 0 0 1-2.8 2.4H9.5a2.8 2.8 0 0 1-2.8-2.4L5.5 9Z" />
        <path d="M9 9 12 3.5 15 9" />
        <path d="M9.7 12.5v4.6M12 12.5v4.6M14.3 12.5v4.6" />
      </svg>
    </span>
    <p>One shared grocery list for the household.</p>
    <button class="btn-primary" onclick={() => void doSignIn()}>Sign in</button>
    {#if signInError}
      <p class="sync-chip" data-status="error">{signInError}</p>
    {/if}
  </div>
{:else}
  {#if status !== 'idle'}
    <div class="sync-chip" data-status={status}>{chipLabel(status)}</div>
  {/if}

  <header class="app-header">
    <div class="brand-side">
      <!-- T009: the basket logo doubles as the LEFT hamburger menu button.
           feature-003 (T008): the active list name sits beside it — the header
           shows NO user name / Sign out (those live in the drawer now). -->
      <button
        class="brand-logo"
        aria-label="Lists"
        aria-haspopup="true"
        aria-expanded={menuOpen}
        onclick={() => (menuOpen = true)}
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path d="M5.5 9h13l-1.2 8.6a2.8 2.8 0 0 1-2.8 2.4H9.5a2.8 2.8 0 0 1-2.8-2.4L5.5 9Z" />
          <path d="M9 9 12 3.5 15 9" />
          <path d="M9.7 12.5v4.6M12 12.5v4.6M14.3 12.5v4.6" />
        </svg>
      </button>
      {#if $activeList}
        <span class="active-list-name">{$activeList.name}</span>
      {/if}
    </div>
  </header>

  <AddBar onadd={(name, quantity) => void addItem({ name, quantity })} />

  <ul class="list">
    {#each $activeItems as item (item.id)}
      <ItemRow
        {item}
        ontoggle={(i) => void toggleTick(i)}
        onedit={(i) => (editing = i)}
        onqty={(i, d) => void bumpQuantity(i, d)}
      />
    {:else}
      <li class="empty-hint">Nothing here yet — add the first item above.</li>
    {/each}
  </ul>

  <div class="footer-actions">
    <button class="btn-ghost" onclick={() => (historyOpen = true)}>History</button>
    <button class="btn-primary" onclick={() => (tripSheetOpen = true)}>Trip done</button>
  </div>

  {#if editing}
    {#key editing.id}
      <ItemEditSheet
        item={editing}
        onsave={(draft) => {
          void saveItemEdit(editing!, draft);
          editing = null;
        }}
        ondelete={() => {
          void deleteItem(editing!.id);
          editing = null;
        }}
        onclose={() => (editing = null)}
      />
    {/key}
  {/if}

  {#if tripSheetOpen}
    <TripDoneSheet
      {tickedCount}
      unticked={untickedItems}
      onconfirm={confirmTrip}
      onclose={() => (tripSheetOpen = false)}
    />
  {/if}

  {#if historyOpen}
    <HistoryDrawer onclose={() => (historyOpen = false)} />
  {/if}

  {#if menuOpen}
    <ListMenu onclose={() => (menuOpen = false)} onsignout={signOut} />
  {/if}
{/if}

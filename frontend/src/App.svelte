<script lang="ts">
  import { onMount } from 'svelte';
  import type { Item, TripDecision } from './lib/db';
  import {
    activeItems,
    userStore,
    addItem,
    toggleTick,
    deleteItem,
    saveItemEdit,
    completeTrip,
    loadStoresFromDb,
    wireStoresToSync,
  } from './lib/stores';
  import { initSyncTriggers, onSyncComplete, runSync } from './lib/sync';
  import { initDb } from './lib/db';
  import { bootstrapServices } from './lib/bootstrap';
  import * as oidc from './lib/oidc';
  import AddBar from './components/AddBar.svelte';
  import ItemRow from './components/ItemRow.svelte';
  import ItemEditSheet from './components/ItemEditSheet.svelte';
  import TripDoneSheet from './components/TripDoneSheet.svelte';
  import HistoryDrawer from './components/HistoryDrawer.svelte';

  type AuthState = 'checking' | 'signedOut' | 'signedIn';

  let auth: AuthState = $state('checking');
  let editing: Item | null = $state(null);
  let tripSheetOpen = $state(false);
  let historyOpen = $state(false);
  let status = $state<'idle' | 'syncing' | 'offline' | 'error'>('idle');

  const untickedItems = $derived(activeItems.filter((i) => !i.ticked));
  const tickedCount = $derived(activeItems.filter((i) => i.ticked).length);

  onMount(async () => {
    onSyncComplete(() => {
      void loadStoresFromDb();
    });
    wireStoresToSync();
    await initDb();
    bootstrapServices();
    await oidc.handleRedirectIfPresent();
    const token = await oidc.ensureFreshToken(); // silent auth (T012)
    const session = await oidc.getSession();
    if (session) {
      userStore.set({ sub: session.sub, name: session.name }); // T022
      auth = 'signedIn';
      await loadStoresFromDb(); // render from IndexedDB first, network-independent
      initSyncTriggers();
      if (token !== null) void runSync('startup');
    } else {
      auth = 'signedOut';
    }
  });

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
  <div class="center-screen"><p>Shoplist…</p></div>
{:else if auth === 'signedOut'}
  <div class="center-screen">
    <h1>Shoplist</h1>
    <p>One shared grocery list for the household.</p>
    <button class="btn-primary" onclick={() => void oidc.startLogin()}>Sign in</button>
  </div>
{:else}
  {#if status !== 'idle'}
    <div class="sync-chip" data-status={status}>{chipLabel(status)}</div>
  {/if}

  <header class="app-header">
    <h1>Shoplist</h1>
    {#if $userStore}
      <span class="user">{$userStore.name}</span>
      <button class="btn-ghost" onclick={signOut}>Sign out</button>
    {/if}
  </header>

  <AddBar onadd={(name, quantity) => void addItem({ name, quantity })} />

  <ul class="list">
    {#each activeItems as item (item.id)}
      <ItemRow {item} ontoggle={(i) => void toggleTick(i)} onedit={(i) => (editing = i)} />
    {:else}
      <li class="empty-hint">Nothing here yet — add the first item above.</li>
    {/each}
  </ul>

  <div class="footer-actions">
    <button class="btn-ghost" onclick={() => (historyOpen = true)}>History</button>
    <button class="btn-primary" onclick={() => (tripSheetOpen = true)}>Trip done</button>
    <span class="user">
      {tickedCount} ticked
    </span>
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
{/if}

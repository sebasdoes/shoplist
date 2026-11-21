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
    reorderItems, // feature-004 (T006)
  } from './lib/stores';
  import { moveItemToIndex, sequenceChanged, slotIndexAt, type RowRect } from './lib/drag';
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
  const tickedItems = $derived($activeItems.filter((i) => i.ticked));

  // ---- feature-004 (T006): pointer-drag reorder of the unticked queue ----
  // The drag starts ONLY from the grip handle (ItemRow), captures the pointer,
  // and re-renders the list live as the pointer crosses row gaps. Touch and
  // mouse ride the same Pointer Events path. Long-press edit (on .item-main)
  // and the tick checkbox never see handle drags.
  let dragId = $state<string | null>(null);
  let dragListId = $state('');
  let dragOrder = $state<string[]>([]);
  /** Layout captured once at drag start: stable slot rectangles per unticked row. */
  let rowRects = $state<RowRect[]>([]);
  let startOrder: string[] = [];
  /** The list <ul> — source of the unticked row rectangles for slot math. */
  let listContainer: HTMLUListElement | null = null;

  const untickedOrder = $derived(untickedItems.map((i) => i.id));
  /** While dragging: the live preview order; otherwise the persisted queue. */
  const untickedDisplay = $derived(dragId ? dragOrder : untickedOrder);
  const untickedById = $derived(new Map(untickedItems.map((i) => [i.id, i])));
  const tickedRows = $derived($activeItems.filter((i) => i.ticked));

  function onHandleDown(id: string, listId: string, event: PointerEvent): void {
    if (dragId || event.button !== 0) return; // already dragging / non-primary
    const container = listContainer;
    if (!container || untickedOrder.length < 2) return; // nothing to rearrange
    const rows = [
      ...container.querySelectorAll<HTMLElement>('li[data-unticked="true"]'),
    ];
    if (rows.length < 2) return;
    rowRects = rows.map((el) => {
      const r = el.getBoundingClientRect();
      return { top: r.top, bottom: r.bottom };
    });
    dragId = id;
    dragListId = listId;
    dragOrder = untickedOrder;
    startOrder = untickedOrder;
    // capture: pointermove/up keep flowing even when the pointer leaves the row
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    window.addEventListener('pointermove', onDragMove);
    window.addEventListener('pointerup', onDragEnd);
    window.addEventListener('pointercancel', onDragEnd);
  }

  function onDragMove(event: PointerEvent): void {
    if (!dragId || event.pointerId === undefined) return;
    const slot = slotIndexAt(rowRects, event.clientY);
    const next = moveItemToIndex(dragOrder, dragId, slot);
    if (sequenceChanged(dragOrder, next)) dragOrder = next; // live preview
  }

  function onDragEnd(event: PointerEvent): void {
    if (!dragId) return;
    window.removeEventListener('pointermove', onDragMove);
    window.removeEventListener('pointerup', onDragEnd);
    window.removeEventListener('pointercancel', onDragEnd);
    const finalOrder = dragOrder;
    const listId = dragListId;
    const moved = sequenceChanged(startOrder, finalOrder);
    dragId = null;
    dragOrder = [];
    rowRects = [];
    startOrder = [];
    if (moved && listId) void reorderItems(listId, finalOrder); // ONE op per drop
  }

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

  <ul class="list" bind:this={listContainer}>
    {#if $activeItems.length === 0}
      <li class="empty-hint">Nothing here yet — add the first item above.</li>
    {:else}
      {#each untickedDisplay as id (id)}
        {@const untickedItem = untickedById.get(id)}
        {#if untickedItem}
          <ItemRow
            item={untickedItem}
            dragging={dragId === id}
            ontoggle={(i) => void toggleTick(i)}
            onedit={(i) => (editing = i)}
            onqty={(i, d) => void bumpQuantity(i, d)}
            onhandledown={(e) => onHandleDown(untickedItem.id, untickedItem.list_id, e)}
          />
        {/if}
      {/each}
      {#each tickedRows as item (item.id)}
        <ItemRow
          {item}
          ontoggle={(i) => void toggleTick(i)}
          onedit={(i) => (editing = i)}
          onqty={(i, d) => void bumpQuantity(i, d)}
        />
      {/each}
    {/if}
  </ul>

  <div class="footer-actions">
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
    <TripDoneSheet ticked={tickedItems} unticked={untickedItems} onconfirm={confirmTrip} onclose={() => (tripSheetOpen = false)} />
  {/if}

  {#if historyOpen}
    <HistoryDrawer onclose={() => (historyOpen = false)} />
  {/if}

  {#if menuOpen}
    <ListMenu
      onclose={() => (menuOpen = false)}
      onsignout={signOut}
      onhistory={() => {
        menuOpen = false;
        historyOpen = true;
      }}
    />
  {/if}
{/if}

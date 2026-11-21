<script lang="ts">
  // T009 — hamburger drawer: every live list, active highlight, '+ New list'
  // input (trim, 1–100, not unique), per-list single-tap delete with the
  // standard 5 s undo snackbar, and the FR-019 last-list refusal message.
  import {
    listsStore,
    activeListId,
    setActiveList,
    createList,
    deleteList,
    undoDeleteList,
    reorderLists,
    sortListsForDisplay,
    userStore,
  } from '../lib/stores';

  // feature-003 (T007): the drawer owns the account section (name + Sign out).
  // feature-005: history access lives here too (main screen footer keeps only Trip done).
  let {
    onclose,
    onsignout,
    onhistory,
  }: { onclose: () => void; onsignout: () => void; onhistory: () => void } = $props();

  let newName = $state('');
  let error = $state<string | null>(null);
  let snackbarListId = $state<string | null>(null);

  // feature-003: rows RENDER in (sort_order asc, created_at asc) order.
  const liveLists = $derived(sortListsForDisplay($listsStore).filter((l) => !l.tombstone));

  function select(id: string) {
    void setActiveList(id); // IndexedDB-only switch (SC-007), no network
    onclose();
  }

  async function submitNew(event: SubmitEvent) {
    event.preventDefault();
    error = null;
    try {
      await createList(newName);
      newName = '';
      onclose(); // the new list is opened immediately (US2-1)
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  function remove(id: string) {
    error = null;
    try {
      void deleteList(id);
      snackbarListId = id;
      setTimeout(() => {
        if (snackbarListId === id) snackbarListId = null;
      }, 5300);
    } catch (e) {
      // FR-019: the LAST list cannot be deleted — show the reason
      error = e instanceof Error ? e.message : String(e);
    }
  }

  function undo() {
    if (!snackbarListId) return;
    void undoDeleteList(snackbarListId);
    snackbarListId = null;
  }

  // feature-003 (T006/T007): up/down tap → rebuild the FULL ordered id array,
  // apply to IndexedDB immediately, enqueue one reorder_lists mutation.
  function move(id: string, delta: number) {
    error = null;
    const ids = liveLists.map((l) => l.id);
    const i = ids.indexOf(id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    try {
      void reorderLists(ids).catch((e) => {
        error = e instanceof Error ? e.message : String(e);
      });
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }
</script>

<button class="menu-backdrop" aria-label="Close lists menu" tabindex="-1" onclick={onclose}
></button>
<nav class="menu-drawer" aria-label="Lists">
  {#if $userStore}
    <!-- feature-005: account section shows only History (left, was the user's
         name) + Sign out (right) — no name display. -->
    <div class="drawer-account">
      <button class="btn-ghost account-history" onclick={onhistory}>History</button>
      <button class="btn-ghost account-signout" onclick={onsignout}>Sign out</button>
    </div>
  {/if}

  <header class="drawer-head">
    <h2>Lists</h2>
    <button class="btn-ghost" onclick={onclose}>Close</button>
  </header>

  <ul class="lists">
    {#each liveLists as list, i (list.id)}
      <li class:active={list.id === $activeListId}>
        <button
          class="list-name"
          aria-current={list.id === $activeListId ? 'true' : undefined}
          onclick={() => select(list.id)}>{list.name}</button
        >
        <button
          class="mv"
          aria-label="Move {list.name} up"
          disabled={i === 0}
          onclick={() => move(list.id, -1)}>↑</button
        >
        <button
          class="mv"
          aria-label="Move {list.name} down"
          disabled={i === liveLists.length - 1}
          onclick={() => move(list.id, 1)}>↓</button
        >
        <button class="del" aria-label="Delete {list.name}" onclick={() => remove(list.id)}
          >✕</button
        >
      </li>
    {:else}
      <li class="empty-hint">No lists yet.</li>
    {/each}
  </ul>

  <form class="new-list" onsubmit={submitNew}>
    <input
      type="text"
      placeholder="New list name"
      aria-label="New list name"
      maxlength={100}
      bind:value={newName}
    />
    <button class="btn-primary" type="submit">+ New list</button>
  </form>
  {#if error}<p class="error" role="alert">{error}</p>{/if}

  {#if snackbarListId}
    <div class="snackbar" role="status">
      List deleted
      <button onclick={undo}>Undo</button>
    </div>
  {/if}
</nav>

<style>
  .menu-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.35);
    border: none;
    padding: 0;
    z-index: 44;
  }
  .menu-drawer {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    width: min(20rem, 85vw);
    background: var(--surface);
    border-right: 1px solid var(--line);
    z-index: 45;
    overflow-y: auto;
    padding: 1rem 1rem calc(2rem + env(safe-area-inset-bottom, 0px));
    display: flex;
    flex-direction: column;
  }
  .drawer-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  h2 {
    margin: 0;
    font-size: 1.15rem;
  }
  .lists {
    list-style: none;
    margin: 0.75rem 0 0 0;
    padding: 0;
  }
  .lists li {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    border-bottom: 1px solid var(--line);
  }
  .list-name {
    flex: 1;
    min-width: 0;
    text-align: left;
    background: none;
    border: none;
    color: var(--ink);
    padding: 0.7rem 0.25rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .lists li.active .list-name {
    color: var(--brand);
    font-weight: 700;
  }
  .del {
    background: none;
    border: 1px solid var(--line);
    border-radius: 999px;
    color: var(--danger);
    width: 2rem;
    height: 2rem;
    flex: 0 0 auto;
  }
  /* feature-003 — up/down reorder arrows (optimistic, offline-first) */
  .mv {
    background: none;
    border: 1px solid var(--line);
    border-radius: 999px;
    color: var(--ink);
    width: 2rem;
    height: 2rem;
    flex: 0 0 auto;
    padding: 0;
    line-height: 1;
  }
  .mv:disabled {
    opacity: 0.35;
    cursor: default;
  }
  .new-list {
    display: flex;
    gap: 0.5rem;
    margin-top: 1rem;
  }
  .new-list input {
    flex: 1;
    min-width: 0;
    background: var(--surface-2);
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 0.5rem 0.9rem;
  }
  .new-list .btn-primary {
    padding: 0.5rem 0.9rem;
    white-space: nowrap;
  }
  .error {
    color: var(--danger);
    font-size: 0.85rem;
    margin: 0.5rem 0 0 0;
  }
  .empty-hint {
    color: var(--muted);
    padding: 0.5rem 0;
  }
</style>

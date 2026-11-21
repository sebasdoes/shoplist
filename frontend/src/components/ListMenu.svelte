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
  } from '../lib/stores';

  let { onclose }: { onclose: () => void } = $props();

  let newName = $state('');
  let error = $state<string | null>(null);
  let snackbarListId = $state<string | null>(null);

  const liveLists = $derived($listsStore.filter((l) => !l.tombstone));

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
</script>

<button class="menu-backdrop" aria-label="Close lists menu" tabindex="-1" onclick={onclose}></button>
<nav class="menu-drawer" aria-label="Lists">
  <header class="drawer-head">
    <h2>Lists</h2>
    <button class="btn-ghost" onclick={onclose}>Close</button>
  </header>

  <ul class="lists">
    {#each liveLists as list (list.id)}
      <li class:active={list.id === $activeListId}>
        <button
          class="list-name"
          aria-current={list.id === $activeListId ? 'true' : undefined}
          onclick={() => select(list.id)}>{list.name}</button
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

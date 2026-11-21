<script lang="ts">
  import { historyVisible, deleteHistoryEntry, undoHistoryDelete } from '../lib/stores';
  import QuickAdd from './QuickAdd.svelte';
  import type { HistoryEntry } from '../lib/db';

  let { onclose }: { onclose: () => void } = $props();

  let snackbarId = $state<string | null>(null);

  // T033: X → optimistic remove + 5 s undo snackbar → DELETE (single tap, no dialog).
  function removeEntry(h: HistoryEntry) {
    void deleteHistoryEntry(h.id);
    snackbarId = h.id;
    setTimeout(() => {
      if (snackbarId === h.id) snackbarId = null;
    }, 5300);
  }

  function undo() {
    if (!snackbarId) return;
    void undoHistoryDelete(snackbarId);
    snackbarId = null;
  }
</script>

<div class="drawer" role="dialog" aria-label="History">
  <header class="drawer-head">
    <h2>History</h2>
    <button class="btn-ghost" onclick={onclose}>Close</button>
  </header>

  <QuickAdd />

  <section>
    <h3>All purchases</h3>
    <ul class="history-list">
      {#each $historyVisible as entry (entry.id)}
        <li>
          <div class="entry-main">
            <span class="name">
              {entry.name}
              {#if entry.outcome === 'not_bought'}<span class="badge">not bought</span>{/if}
            </span>
            {#if entry.quantity}<span class="qty">{entry.quantity}</span>{/if}
            {#if entry.note}<span class="note">{entry.note}</span>{/if}
            <span class="when">
              {new Date(entry.occurred_at).toLocaleDateString()}
            </span>
          </div>
          <button
            class="del"
            aria-label="Delete {entry.name} from history"
            onclick={() => removeEntry(entry)}>✕</button
          >
        </li>
      {:else}
        <li class="empty-hint">No purchases yet.</li>
      {/each}
    </ul>
  </section>

  {#if snackbarId}
    <div class="snackbar" role="status">
      Entry removed
      <button onclick={undo}>Undo</button>
    </div>
  {/if}
</div>

<style>
  .drawer-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  h2 {
    margin: 0;
    font-size: 1.15rem;
  }
  h3 {
    font-size: 0.9rem;
    color: var(--muted);
    margin: 1.5rem 0 0.5rem 0;
  }
  .history-list {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .history-list li {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    border-bottom: 1px solid var(--line);
    padding: 0.6rem 0;
  }
  .entry-main {
    flex: 1;
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.4rem;
    min-width: 0;
  }
  .name {
    font-weight: 600;
  }
  .badge {
    font-size: 0.7rem;
    color: var(--muted);
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 0 0.4rem;
  }
  .qty {
    color: var(--brand);
    font-size: 0.85rem;
  }
  .note {
    color: var(--muted);
    font-size: 0.85rem;
  }
  .when {
    color: var(--muted);
    font-size: 0.75rem;
    margin-left: auto;
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
  .empty-hint {
    color: var(--muted);
    padding: 0.5rem 0;
  }
</style>

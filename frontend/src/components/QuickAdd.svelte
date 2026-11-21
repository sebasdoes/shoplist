<script lang="ts">
  import type { Suggestion } from '../lib/rank';
  import { rankSuggestions } from '../lib/rank';
  import { historySorted, activeItems, reAddFromHistory, deleteHistoryEntry, undoHistoryDelete } from '../lib/stores';

  let { onreadd }: { onreadd?: (s: Suggestion) => void } = $props();

  // T033 — decayed-frequency ranking over the history store; not_bought and
  // names already on the list are excluded.
  const activeNameKeys = $derived(new Set($activeItems.map((i) => i.name_key)));
  const suggestions = $derived(rankSuggestions($historySorted, Date.now(), activeNameKeys));

  // ✕ tap: remove ALL history rows behind the suggestion (the chip is an
  // aggregate of every purchase with that name) — optimistic, 5 s undo each.
  let removed = $state<{ name: string; ids: string[] } | null>(null);
  let removeTimer: ReturnType<typeof setTimeout> | null = null;
  const UNDO_MS = 5300;

  async function removeSuggestion(s: Suggestion) {
    const ids = [...s.historyIds];
    if (ids.length === 0) return;
    for (const id of ids) {
      await deleteHistoryEntry(id);
    }
    if (removeTimer) clearTimeout(removeTimer);
    removed = { name: s.name, ids };
    removeTimer = setTimeout(() => (removed = null), UNDO_MS);
  }

  async function undoRemove() {
    if (!removed) return;
    if (removeTimer) {
      clearTimeout(removeTimer);
      removeTimer = null;
    }
    const ids = removed.ids;
    removed = null;
    for (const id of ids) {
      await undoHistoryDelete(id);
    }
  }
</script>

<section class="quick-add">
  <h3>Quick add</h3>
  {#if suggestions.length === 0}
    <p class="empty-hint">Suggestions appear after your first trip.</p>
  {:else}
    <ul>
      {#each suggestions as s (s.nameKey)}
        <li class="suggestion-item">
          <!-- ✕ on the left: drops the purchase from history (with 5 s undo). -->
          <button
            class="sug-del"
            aria-label="Remove {s.name} from history"
            onclick={() => void removeSuggestion(s)}
          >✕</button>
          <button
            class="suggestion"
            onclick={() => void reAddFromHistory(s).then(() => onreadd?.(s))}
          >
            <span class="name">
              {s.name}
              {#if s.staple}<span class="staple" title="Staple">★</span>{/if}
            </span>
            {#if s.quantity}<span class="qty">{s.quantity}</span>{/if}
            {#if s.note}<span class="note">{s.note}</span>{/if}
          </button>
        </li>
      {/each}
    </ul>
  {/if}

  {#if removed}
    <div class="snackbar" role="status">
      Removed “{removed.name}” from history
      <button onclick={() => void undoRemove()}>Undo</button>
    </div>
  {/if}
</section>

<style>
  h3 {
    font-size: 0.9rem;
    color: var(--muted);
    margin: 0.5rem 0 0.5rem 0;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  .suggestion-item {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
  }
  .sug-del {
    background: none;
    border: 1px solid var(--line);
    border-radius: 999px;
    color: var(--muted);
    width: 1.35rem;
    height: 1.35rem;
    flex: 0 0 auto;
    padding: 0;
    line-height: 1;
    font-size: 0.75rem;
  }
  .suggestion {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.4rem;
    background: var(--surface-2);
    border: 1px solid var(--line);
    color: var(--ink);
    border-radius: 999px;
    padding: 0.4rem 0.9rem;
  }
  .name {
    font-weight: 600;
  }
  .qty {
    color: var(--brand);
    font-size: 0.8rem;
  }
  .note {
    color: var(--muted);
    font-size: 0.8rem;
  }
  .staple {
    color: var(--warn);
    font-size: 0.8rem;
  }
  .empty-hint {
    color: var(--muted);
  }
  .snackbar {
    margin-top: 0.75rem;
    display: flex;
    align-items: center;
    gap: 1rem;
    background: var(--surface-2);
    color: var(--ink);
    border-radius: 999px;
    padding: 0.5rem 1rem;
    font-size: 0.85rem;
    width: 100%;
  }
  .snackbar button {
    background: none;
    border: none;
    color: var(--brand);
    font-weight: 600;
    padding: 0;
  }
</style>

<script lang="ts">
  import { historySorted, activeItems, reAddFromHistory } from '../lib/stores';
  import { rankSuggestions } from '../lib/rank';
  import type { Suggestion } from '../lib/rank';

  let { onreadd }: { onreadd?: (s: Suggestion) => void } = $props();

  // T033 — decayed-frequency ranking over the history store; not_bought and
  // names already on the list are excluded.
  const activeNameKeys = $derived(new Set($activeItems.map((i) => i.name_key)));
  const suggestions = $derived(rankSuggestions($historySorted, Date.now(), activeNameKeys));
</script>

<section class="quick-add">
  <h3>Quick add</h3>
  {#if suggestions.length === 0}
    <p class="empty-hint">Suggestions appear after your first trip.</p>
  {:else}
    <ul>
      {#each suggestions as s (s.nameKey)}
        <li>
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
  .suggestion {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.4rem;
    background: #eef4f0;
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
    color: #d99a17;
    font-size: 0.8rem;
  }
  .empty-hint {
    color: var(--muted);
  }
</style>

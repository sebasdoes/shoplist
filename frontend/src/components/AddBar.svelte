<script lang="ts">
  import { MAX_NAME } from '../lib/validate';
  import { searchHistory } from '../lib/rank';
  import { historySorted, activeItems } from '../lib/stores';

  // Quantity is intentionally NOT part of the add bar: typing room goes to the
  // name, new items default to "1", and the amount is edited on the row later.
  let { onadd }: { onadd: (name: string, quantity: string | null) => void } = $props();

  let name = $state('');
  let error = $state<string | null>(null);

  // feature-006: type-to-search over history. Typing 'big' suggests
  // 'Big American Texas'; tapping fills the add with the remembered name and
  // quantity. Already-active names are excluded (use the row or Quick add).
  const activeNameKeys = $derived(new Set($activeItems.map((i) => i.name_key)));
  const matches = $derived(searchHistory($historySorted, name, Date.now(), activeNameKeys).slice(0, 6));
  const showMatches = $derived(name.trim().length > 0 && matches.length > 0);

  function submit(e: SubmitEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      error = 'Name is required';
      return;
    }
    if (trimmed.length > MAX_NAME) {
      error = `Name must be at most ${MAX_NAME} characters`;
      return;
    }
    error = null;
    onadd(trimmed, '1');
    name = '';
  }

  function pick(s: { name: string; quantity: string | null }) {
    error = null;
    onadd(s.name, s.quantity ?? '1');
    name = '';
  }
</script>

<form class="add-bar" onsubmit={submit}>
  <input
    class="name"
    type="text"
    aria-label="Item name"
    placeholder="Add an item…"
    bind:value={name}
    oninput={() => (error = null)}
  />
  <button type="submit" class="btn-primary">Add</button>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if showMatches}
    <ul class="typeahead" role="listbox" aria-label="History matches">
      {#each matches as s (s.nameKey)}
        <li>
          <button type="button" class="match" onclick={() => pick(s)}>
            <span class="name">{s.name}</span>
            {#if s.quantity}<span class="qty">{s.quantity}</span>{/if}
            {#if s.note}<span class="note">{s.note}</span>{/if}
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</form>

<style>
  .add-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    padding: 0 1rem;
    align-items: center;
  }
  .add-bar .name {
    flex: 1;
    min-width: 0;
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 0.7rem 1rem;
    background: var(--surface);
  }
  .typeahead {
    list-style: none;
    margin: 0.35rem 0 0 0;
    padding: 0;
    flex-basis: 100%;
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }
  .match {
    display: inline-flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.35rem;
    background: var(--surface-2);
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 0.35rem 0.75rem;
    color: var(--ink);
    font-size: 0.9rem;
  }
  .match .qty {
    color: var(--muted);
    font-size: 0.8rem;
  }
  .match .note {
    color: var(--muted);
    font-size: 0.8rem;
    font-style: italic;
  }
  .error {
    color: var(--danger);
    font-size: 0.8rem;
    margin: 0.35rem 0 0 0.25rem;
    flex-basis: 100%;
  }
</style>

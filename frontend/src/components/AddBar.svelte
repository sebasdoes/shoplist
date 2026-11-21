<script lang="ts">
  import { MAX_NAME } from '../lib/validate';

  // Quantity is intentionally NOT part of the add bar: typing room goes to the
  // name, new items default to "1", and the amount is edited on the row later.
  let { onadd }: { onadd: (name: string, quantity: string | null) => void } = $props();

  let name = $state('');
  let error = $state<string | null>(null);

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
</form>

<style>
  .add-bar {
    display: flex;
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
  .error {
    color: var(--danger);
    font-size: 0.8rem;
    margin: 0.35rem 0 0 0.25rem;
    flex-basis: 100%;
  }
</style>

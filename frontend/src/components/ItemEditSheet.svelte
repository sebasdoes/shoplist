<script lang="ts">
  import type { Item } from '../lib/db';
  import { MAX_NOTE, MAX_NAME, MAX_QUANTITY, nameKey } from '../lib/validate';

  let {
    item,
    onsave,
    ondelete,
    onclose,
  }: {
    item: Item;
    onsave: (draft: {
      name: string;
      quantity: string | null;
      note: string | null;
      staple: boolean;
    }) => void;
    ondelete: () => void;
    onclose: () => void;
  } = $props();

  // The sheet is remounted per editing session ({#key} in the app shell), so
  // seeding local form state from the initial props is intentional.
  /* svelte-ignore state_referenced_locally */
  let name = $state(item.name);
  /* svelte-ignore state_referenced_locally */
  let quantity = $state(item.quantity ?? '');
  /* svelte-ignore state_referenced_locally */
  let note = $state(item.note ?? '');
  /* svelte-ignore state_referenced_locally */
  let staple = $state(item.staple);
  let error = $state<string | null>(null);

  const nameTrimmed = $derived(name.trim());
  const canSave = $derived(nameTrimmed.length > 0 && !error);

  // Validation mirrors data-model.md: name 1–200 after trim, quantity ≤50, note ≤200.
  $effect(() => {
    if (!nameTrimmed) error = 'Name is required';
    else if (nameTrimmed.length > MAX_NAME) error = `Name must be at most ${MAX_NAME} characters`;
    else if (quantity.trim().length > MAX_QUANTITY)
      error = `Quantity must be at most ${MAX_QUANTITY} characters`;
    else if (note.trim().length > MAX_NOTE) error = `Note must be at most ${MAX_NOTE} characters`;
    else error = null;
  });

  function save() {
    if (!canSave) return;
    // Uniqueness guard: keep display name usable; the server dedupes by name_key.
    void nameKey(nameTrimmed);
    onsave({
      name: nameTrimmed,
      quantity: quantity.trim() || null,
      note: note.trim() || null,
      staple,
    });
  }
</script>

<div class="sheet-backdrop" role="dialog" aria-label="Edit item">
  <div class="sheet" role="document">
    <h2>Edit item</h2>

    <label>
      Name
      <input type="text" bind:value={name} maxlength={MAX_NAME + 10} />
    </label>

    <label>
      Quantity
      <input
        type="text"
        bind:value={quantity}
        placeholder="2x, 500g"
        maxlength={MAX_QUANTITY + 10}
      />
    </label>

    <label>
      Note
      <textarea bind:value={note} rows="2" maxlength={MAX_NOTE + 10} placeholder="the small ones"
      ></textarea>
    </label>

    <label class="staple-row">
      <input type="checkbox" bind:checked={staple} />
      Staple — re-add automatically after every trip
    </label>

    {#if error}<p class="error" role="alert">{error}</p>{/if}

    <div class="actions">
      <button class="btn-ghost danger" onclick={ondelete}>Delete</button>
      <button class="btn-ghost" onclick={onclose}>Cancel</button>
      <button class="btn-primary" disabled={!canSave} onclick={save}>Save</button>
    </div>
  </div>
</div>

<style>
  h2 {
    margin: 0 0 0.75rem 0;
    font-size: 1.05rem;
  }
  label {
    display: block;
    margin-bottom: 0.75rem;
    font-size: 0.8rem;
    color: var(--muted);
  }
  input[type='text'],
  textarea {
    display: block;
    width: 100%;
    margin-top: 0.25rem;
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 0.6rem 0.75rem;
    background: var(--bg);
    font-size: 1rem;
    color: var(--ink);
  }
  .staple-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    color: var(--ink);
    font-size: 0.9rem;
  }
  .staple-row input {
    width: 1.25rem;
    height: 1.25rem;
    accent-color: var(--brand);
  }
  .error {
    color: var(--danger);
    font-size: 0.8rem;
    margin: 0;
  }
  .actions {
    display: flex;
    gap: 0.5rem;
    margin-top: 1rem;
    justify-content: flex-end;
  }
  .danger {
    color: var(--danger);
    margin-right: auto;
  }
  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
</style>

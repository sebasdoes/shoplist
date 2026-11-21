<script lang="ts">
  import type { Item } from '../lib/db';

  let {
    item,
    ontoggle,
    onedit,
    onqty,
  }: {
    item: Item;
    ontoggle: (item: Item) => void;
    onedit: (item: Item) => void;
    onqty: (item: Item, delta: number) => void;
  } = $props();

  let pressTimer: ReturnType<typeof setTimeout> | undefined;

  // T030: long-press opens the edit sheet.
  function pressStart() {
    pressTimer = setTimeout(() => onedit(item), 500);
  }
  function pressEnd() {
    if (pressTimer) {
      clearTimeout(pressTimer);
      pressTimer = undefined;
    }
  }
</script>

<li class="item-row" class:ticked={item.ticked} data-id={item.id}>
  <input
    type="checkbox"
    class="tick"
    checked={item.ticked}
    aria-label={item.ticked ? `Untick ${item.name}` : `Tick ${item.name}`}
    onchange={() => ontoggle(item)}
  />
  <button
    class="item-main"
    onclick={() => onedit(item)}
    onpointerdown={pressStart}
    onpointerup={pressEnd}
    onpointerleave={pressEnd}
  >
    <span class="name">
      {item.name}
      {#if item.staple}<span class="staple" title="Staple">★</span>{/if}
    </span>
    {#if item.note}<span class="note">{item.note}</span>{/if}
  </button>
  <div class="qty-side">
    <button
      type="button"
      class="step"
      aria-label={`Remove one ${item.name}`}
      disabled={!/^-?\d+/.test(item.quantity ?? '')}
      onclick={() => onqty(item, -1)}
    >−</button>
    <span class="qty" role="spinbutton" aria-label={`Amount of ${item.name}`}>{item.quantity ?? '1'}</span>
    <button
      type="button"
      class="step"
      aria-label={`Add one ${item.name}`}
      onclick={() => onqty(item, 1)}
    >+</button>
  </div>
</li>

<style>
  .item-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    background: var(--surface);
    border-radius: var(--radius);
    padding: 0.8rem 0.9rem;
    margin-bottom: 0.6rem;
    border: 1px solid var(--line);
  }
  .tick {
    /* Polished custom checkbox: dark surface, brand fill + check when ticked
       (the UA default rendered a jarring white box unchecked on dark). */
    appearance: none;
    -webkit-appearance: none;
    width: 1.6rem;
    height: 1.6rem;
    margin: 0;
    border: 2px solid var(--muted);
    border-radius: 0.5rem;
    background: var(--surface-2);
    cursor: pointer;
    flex-shrink: 0;
    display: inline-grid;
    place-content: center;
    transition:
      background 0.15s ease,
      border-color 0.15s ease;
  }
  .tick::before {
    content: "";
    width: 0.95rem;
    height: 0.95rem;
    transform: scale(0);
    transition: transform 0.12s ease-in-out;
  }
  .tick:checked {
    background: var(--brand);
    border-color: var(--brand);
  }
  .tick:checked::before {
    transform: scale(1);
    background: var(--brand-ink);
    clip-path: polygon(14% 44%, 0 65%, 50% 100%, 100% 16%, 80% 3%, 46% 63%);
  }
  .item-main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.45rem;
    background: none;
    border: none;
    padding: 0;
    text-align: left;
    color: inherit;
  }
  .name {
    font-weight: 600;
  }
  .qty {
    color: var(--brand);
    font-weight: 600;
    font-size: 0.85rem;
    min-width: 2.6rem;
    text-align: center;
  }
  .qty-side {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 0.15rem;
    flex-shrink: 0;
  }
  .step {
    width: 1.9rem;
    height: 1.9rem;
    border-radius: 999px;
    border: 1px solid var(--line);
    background: var(--surface-2);
    color: var(--ink);
    font: inherit;
    font-size: 1rem;
    line-height: 1;
    padding: 0;
  }
  .step:disabled {
    opacity: 0.35;
  }
  .note {
    color: var(--muted);
    font-size: 0.85rem;
  }
  .staple {
    color: var(--warn);
    font-size: 0.85rem;
  }
  .ticked .name {
    text-decoration: line-through;
    color: var(--muted);
  }
  .ticked {
    opacity: 0.75;
    background: var(--surface);
  }
</style>

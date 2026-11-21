<script lang="ts">
  import type { Item } from '../lib/db';

  let {
    item,
    ontoggle,
    onedit,
  }: {
    item: Item;
    ontoggle: (item: Item) => void;
    onedit: (item: Item) => void;
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
    {#if item.quantity}<span class="qty">{item.quantity}</span>{/if}
    {#if item.note}<span class="note">{item.note}</span>{/if}
  </button>
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
    width: 1.5rem;
    height: 1.5rem;
    accent-color: var(--brand);
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
    background: #eef4f0;
    border-radius: 999px;
    padding: 0.1rem 0.55rem;
  }
  .note {
    color: var(--muted);
    font-size: 0.85rem;
  }
  .staple {
    color: #d99a17;
    font-size: 0.85rem;
  }
  .ticked .name {
    text-decoration: line-through;
    color: var(--muted);
  }
  .ticked {
    opacity: 0.75;
    background: #efede6;
  }
</style>

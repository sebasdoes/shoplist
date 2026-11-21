<script lang="ts">
  import type { Item, TripDecision } from '../lib/db';

  // T008 fix: the sheet receives the TICKED items themselves — each becomes a
  // 'bought' trip decision. Without them the trip never archives anything
  // (the regression: ticked items stayed crossed out forever).
  let {
    ticked,
    unticked,
    onconfirm,
    onclose,
  }: {
    ticked: Item[];
    unticked: Item[];
    onconfirm: (decisions: TripDecision[]) => void;
    onclose: () => void;
  } = $props();

  let step = $state(0);
  let decisions = $state<TripDecision[]>([]);
  // Bought (ticked) decisions are built up front; unticked items then get
  // ONE-tap keep/drop, presented one at a time (US2-2).
  const bought = $derived(
    ticked.map((i) => ({ item_id: i.id, outcome: 'bought' as const })),
  );
  const current = $derived(unticked[step] ?? null);

  function finish() {
    onconfirm([...bought, ...decisions]);
  }
</script>

<div class="sheet-backdrop" role="dialog" aria-label="Trip done">
  <div class="sheet" role="document">
    {#if ticked.length === 0 && unticked.length === 0}
      <h2>Nothing to finish</h2>
      <div class="actions">
        <button class="btn-ghost" onclick={onclose}>Close</button>
      </div>
    {:else if current}
      <p class="progress">{step + 1} of {unticked.length} still on the list</p>
      <h2>“{current.name}”</h2>
      {#if current.quantity}<p class="meta">{current.quantity}</p>{/if}
      <p class="meta">{ticked.length} going to history. Bought it? If not — keep or drop.</p>
      <div class="actions">
        <button
          class="btn-ghost"
          onclick={() => {
            decisions = [...decisions, { item_id: current.id, outcome: 'kept' }];
            step += 1;
          }}
        >Keep on list</button>
        <button
          class="btn-primary"
          onclick={() => {
            decisions = [...decisions, { item_id: current.id, outcome: 'not_bought' }];
            step += 1;
          }}
        >Drop</button>
      </div>
    {:else}
      <h2>Trip done</h2>
      <p>
        {ticked.length} item{ticked.length === 1 ? '' : 's'} go to history.
        {#if decisions.length > 0}{decisions.length} decisions queued.{:else if unticked.length === 0}Nothing left to decide.{/if}
      </p>
      <div class="actions">
        <button class="btn-ghost" onclick={onclose}>Cancel</button>
        <button class="btn-primary" onclick={finish}>Done</button>
      </div>
    {/if}
  </div>
</div>

<style>
  h2 {
    margin: 0 0 0.5rem 0;
    font-size: 1.1rem;
  }
  .progress {
    color: var(--muted);
    font-size: 0.8rem;
    margin: 0 0 0.5rem 0;
  }
  .meta {
    color: var(--muted);
    font-size: 0.9rem;
    margin: 0.2rem 0;
  }
  .actions {
    display: flex;
    gap: 1rem;
    margin-top: 1.25rem;
    justify-content: space-between;
  }
  .actions .btn-primary,
  .actions .btn-ghost {
    flex: 1;
  }
</style>

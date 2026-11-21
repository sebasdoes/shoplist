<script lang="ts">
  import type { Item, TripDecision } from '../lib/db';

  let {
    tickedCount,
    unticked,
    onconfirm,
    onclose,
  }: {
    tickedCount: number;
    unticked: Item[];
    onconfirm: (decisions: TripDecision[]) => void;
    onclose: () => void;
  } = $props();

  let step = $state(0);
  let decisions = $state<TripDecision[]>([]);

  // Unticked items get ONE-tap keep/drop, presented one at a time (US2-2).
  const finished = $derived(step >= unticked.length);
  const current = $derived(unticked[step] ?? null);

  function decide(outcome: 'kept' | 'not_bought') {
    if (!current) return;
    decisions = [...decisions, { item_id: current.id, outcome }];
    step += 1;
  }

  // Whole trip → ONE outbox unit; server-side idempotent retry (US2-5).
  function finish() {
    if (!finished) return;
    onconfirm(decisions);
  }
</script>

<div class="sheet-backdrop" role="dialog" aria-label="Trip done">
  <div class="sheet" role="document">
    {#if !finished && current}
      <p class="progress">{step + 1} of {unticked.length} still on the list</p>
      <h2>“{current.name}”</h2>
      {#if current.quantity}<p class="meta">{current.quantity}</p>{/if}
      <p class="meta">Bought it? If not — keep it on the list or drop it.</p>
      <div class="actions">
        <button class="btn-ghost" onclick={() => decide('kept')}>Keep on list</button>
        <button class="btn-primary" onclick={() => decide('not_bought')}>Drop</button>
      </div>
    {:else}
      <h2>Trip done</h2>
      <p>
        {tickedCount} item{tickedCount === 1 ? '' : 's'} go to history.
        {#if unticked.length === 0}Nothing left to decide.{/if}
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

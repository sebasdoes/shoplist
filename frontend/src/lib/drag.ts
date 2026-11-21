// feature-004 (T006) — pure pointer-drag math for the unticked queue.
// Kept out of the .svelte components so vitest (node env) can cover it:
// the component glue only forwards pointer events + row rects into these.
export interface RowRect {
  top: number;
  bottom: number;
}

/**
 * Insertion slot under a pointer y, in "gap" semantics against the layout
 * captured at drag start:
 *   top half of slot i   → insert before slot i       (index i)
 *   bottom half of slot i → insert after slot i        (index i + 1)
 * Below the last slot centre → index rects.length (after the last row).
 */
export function slotIndexAt(rects: RowRect[], y: number): number {
  for (let i = 0; i < rects.length; i++) {
    const height = rects[i].bottom - rects[i].top;
    if (y < rects[i].top + height / 2) return i;
  }
  return rects.length;
}

/**
 * New ordering with `id` moved to `index` (a slot index in the
 * without-the-dragged-item coordinate space, clamped to the valid range).
 */
export function moveItemToIndex(order: string[], id: string, index: number): string[] {
  const next = order.filter((x) => x !== id);
  const clamped = Math.max(0, Math.min(index, next.length));
  next.splice(clamped, 0, id);
  return next;
}

/** Whether two orderings differ (used to skip no-op drags — no mutation, no op). */
export function sequenceChanged(before: string[], after: string[]): boolean {
  return before.join('\u0000') !== after.join('\u0000');
}

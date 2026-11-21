// Quantity stepper logic (pure, unit-tested): the row +/- buttons bump the
// FIRST integer inside the free-text quantity, preserving any surrounding text
// ("2x" -> "3x", "500g" -> "501g"). Non-numeric or already-1 quantities won't
// go below 1 — free-form editing stays in the edit sheet.

export function bumpQuantityText(quantity: string | null, delta: number): string | null {
  const cur = (quantity ?? '1').trim() || '1';
  const m = cur.match(/-?\d+/);
  if (!m) return cur; // no number to bump (e.g. "a lot"): edit sheet territory
  const n = Math.max(1, Number(m[0]) + delta);
  if (Number(m[0]) === n) return cur; // clamped at 1, nothing changed
  return cur.replace(m[0], String(n));
}

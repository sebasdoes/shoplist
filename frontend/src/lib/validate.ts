// Validation + name normalization mirroring data-model.md (identical client/server).

export const MAX_NAME = 200;
export const MAX_QUANTITY = 50;
export const MAX_NOTE = 200;

/** research D6: casefold + trim + collapse whitespace + NFKD diacritic strip. */
export function nameKey(name: string): string {
  return name.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().trim().replace(/\s+/g, ' ');
}

export function validateItem(fields: {
  name: string;
  quantity: string | null;
  note: string | null;
}): string | null {
  const name = fields.name.trim();
  if (!name) return 'Name is required';
  if (name.length > MAX_NAME) return `Name must be at most ${MAX_NAME} characters`;
  if ((fields.quantity ?? '').length > MAX_QUANTITY)
    return `Quantity must be at most ${MAX_QUANTITY} characters`;
  if ((fields.note ?? '').length > MAX_NOTE) return `Note must be at most ${MAX_NOTE} characters`;
  return null;
}

export function uuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  throw new Error('crypto.randomUUID unavailable');
}

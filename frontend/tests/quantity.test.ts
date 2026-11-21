// Quantity stepper logic: +/− bumps the first integer inside the free-text
// quantity, keeps surrounding text, clamps at 1. Non-numeric stays put.
import { describe, expect, it } from 'vitest';
import { bumpQuantityText } from '../src/lib/quantity';

describe('bumpQuantityText', () => {
  it('bumps plain numbers', () => {
    expect(bumpQuantityText('1', 1)).toBe('2');
    expect(bumpQuantityText('3', -1)).toBe('2');
  });

  it('bumps the number inside suffixed quantities and keeps text', () => {
    expect(bumpQuantityText('2x', 1)).toBe('3x');
    expect(bumpQuantityText('500g', -1)).toBe('499g');
    expect(bumpQuantityText('4 pk', 1)).toBe('5 pk');
  });

  it('clamps at 1 — no zero, no negatives', () => {
    expect(bumpQuantityText('1', -1)).toBe('1');
  });

  it('treats missing quantity as 1', () => {
    expect(bumpQuantityText(null, 1)).toBe('2');
  });

  it('leaves non-numeric quantities untouched', () => {
    expect(bumpQuantityText('een paar', 1)).toBe('een paar');
  });
});

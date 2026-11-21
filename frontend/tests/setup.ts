// Vitest setup: in-memory IndexedDB + per-test isolation.
import 'fake-indexeddb/auto';
import { afterEach, vi } from 'vitest';
import * as db from '../src/lib/db';
import { resetStoresForTests } from '../src/lib/stores';
import { clearPostMutationTimerForTests } from '../src/lib/sync';

afterEach(async () => {
  clearPostMutationTimerForTests();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  resetStoresForTests();
  await db.resetForTests();
});

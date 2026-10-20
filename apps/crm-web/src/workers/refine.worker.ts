/// <reference lib="webworker" />
/** Instant Refine preview off the main thread (§10.5). The server re-runs the same engine on save. */
import { refineRow } from '@tfsc/refine';

self.onmessage = (e: MessageEvent<{ id: number; rows: Record<string, unknown>[]; today: string }>) => {
  const t0 = performance.now();
  const out = e.data.rows.map((r) => refineRow(r, e.data.today));
  (self as unknown as Worker).postMessage({ id: e.data.id, rows: out, ms: performance.now() - t0 });
};

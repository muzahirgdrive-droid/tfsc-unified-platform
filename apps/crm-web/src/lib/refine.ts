import type { RefinedRow } from '@tfsc/refine';
import { businessDate } from './time';

let worker: Worker | null = null;
let seq = 0;
const pending = new Map<number, (v: { rows: RefinedRow[]; ms: number }) => void>();

export function refineInWorker(rows: Record<string, unknown>[]): Promise<{ rows: RefinedRow[]; ms: number }> {
  if (!worker) {
    worker = new Worker(new URL('../workers/refine.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = (e: MessageEvent<{ id: number; rows: RefinedRow[]; ms: number }>) => { pending.get(e.data.id)?.(e.data); pending.delete(e.data.id); };
  }
  const id = ++seq;
  return new Promise((resolve) => { pending.set(id, resolve); worker!.postMessage({ id, rows, today: businessDate() }); });
}

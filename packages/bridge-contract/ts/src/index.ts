/**
 * TFSC <-> CRM signed integration contract (TypeScript). Mirror of ../python/tfsc_bridge.
 * message = [timestamp, nonce, METHOD, path, sha256hex(body)].join("\n"); signature = hex HMAC-SHA256.
 */
import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';

export const TOLERANCE_S = 300;
const NONCE_RE = /^[A-Za-z0-9_-]{16,100}$/;

export class BridgeError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

const sortKeys = (v: unknown): unknown => {
  if (Array.isArray(v)) return v.map(sortKeys);
  if (v && typeof v === 'object') return Object.fromEntries(Object.keys(v as object).sort().map((k) => [k, sortKeys((v as Record<string, unknown>)[k])]));
  return v;
};

/** Bytes actually sent; identical to Python json.dumps(sort_keys=True, separators=(',', ':'), ensure_ascii=False). */
export function canonicalBody(payload: unknown): Buffer {
  return Buffer.from(JSON.stringify(sortKeys(payload)), 'utf8');
}

export function sign(secret: string, method: string, path: string, body: Buffer | string, timestamp: string, nonce: string): string {
  const b = typeof body === 'string' ? Buffer.from(body, 'utf8') : body;
  const msg = [timestamp, nonce, method.toUpperCase(), path, createHash('sha256').update(b).digest('hex')].join('\n');
  return createHmac('sha256', secret).update(msg).digest('hex');
}

export function headers(secret: string, method: string, path: string, body: Buffer, opts: { eventId?: string; schema?: string; now?: number; nonce?: string } = {}) {
  if (secret.length < 32) throw new BridgeError(503, 'Bridge secret must be at least 32 characters.');
  const ts = String(Math.floor(opts.now ?? Date.now() / 1000));
  const nonce = opts.nonce ?? randomBytes(18).toString('base64url');
  const h: Record<string, string> = { 'Content-Type': 'application/json', 'X-TFSC-Timestamp': ts, 'X-TFSC-Nonce': nonce, 'X-TFSC-Signature': sign(secret, method, path, body, ts, nonce) };
  if (opts.eventId) h['X-TFSC-Event-Id'] = opts.eventId;
  if (opts.schema) h['X-TFSC-Schema'] = opts.schema;
  return h;
}

export function verify(secret: string, method: string, path: string, body: Buffer, hdrs: Record<string, string>, opts: { now?: number; seenNonce?: (n: string) => boolean } = {}): void {
  if (secret.length < 32) throw new BridgeError(503, 'Bridge is not configured with a sufficiently strong shared secret.');
  const low = Object.fromEntries(Object.entries(hdrs).map(([k, v]) => [k.toLowerCase(), v]));
  const ts = low['x-tfsc-timestamp'] ?? ''; const nonce = low['x-tfsc-nonce'] ?? ''; const sig = low['x-tfsc-signature'] ?? '';
  const now = opts.now ?? Date.now() / 1000;
  const fresh = /^\d+$/.test(ts) && Math.abs(now - Number(ts)) <= TOLERANCE_S;
  if (!fresh || !NONCE_RE.test(nonce)) throw new BridgeError(401, 'Invalid bridge authentication.');
  const expected = Buffer.from(sign(secret, method, path, body, ts, nonce));
  const given = Buffer.from(sig);
  if (given.length !== expected.length || !timingSafeEqual(given, expected)) throw new BridgeError(401, 'Invalid bridge authentication.');
  if (opts.seenNonce && opts.seenNonce(nonce)) throw new BridgeError(409, 'Bridge request was already received.');
}

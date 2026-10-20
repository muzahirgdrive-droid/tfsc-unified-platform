import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { BridgeError, canonicalBody, headers, sign, verify } from '../src/index';

type Vec = { secret: string; method: string; path: string; body: string; timestamp: string; nonce: string; signature: string };
const VECS: Vec[] = JSON.parse(readFileSync(new URL('../../signature-vectors.json', import.meta.url), 'utf8'));
const SECRET = 'x'.repeat(40);

describe('bridge contract (identical to Python)', () => {
  it.each(VECS.map((v) => [v] as [Vec]))('signature vector %#', (v) => {
    expect(sign(v.secret, v.method, v.path, v.body, v.timestamp, v.nonce)).toBe(v.signature);
  });
  it('canonical body matches Python', () => {
    expect(canonicalBody({ b: 2, a: { d: 1, c: 'é' } }).toString('utf8')).toBe('{"a":{"c":"é","d":1},"b":2}');
  });
  it('rejects replay, expiry and tampering', () => {
    const body = canonicalBody({ a: 1 });
    const h = headers(SECRET, 'POST', '/x', body, { now: 1000 });
    const seen = new Set<string>();
    const once = (n: string) => { const d = seen.has(n); seen.add(n); return d; };
    verify(SECRET, 'POST', '/x', body, h, { now: 1100, seenNonce: once });
    expect(() => verify(SECRET, 'POST', '/x', body, h, { now: 1100, seenNonce: once })).toThrow(BridgeError);
    expect(() => verify(SECRET, 'POST', '/x', body, h, { now: 1301 })).toThrow(/Invalid/);
    expect(() => verify(SECRET, 'POST', '/x', Buffer.from('{"a":2}'), h, { now: 1000 })).toThrow(/Invalid/);
  });
});

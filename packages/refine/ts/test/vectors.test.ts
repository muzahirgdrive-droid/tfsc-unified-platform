import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { parseDate, parseTri, refineEmail, refineName, refinePhone, refineRow } from '../src/index';

type Case = { input: any; expected?: any; error?: boolean };
const V: { today: string; names: Case[]; phones: Case[]; emails: Case[]; dates: Case[]; tris: Case[]; rows: Case[] } = JSON.parse(readFileSync(new URL('../../vectors.json', import.meta.url), 'utf8'));

describe('shared vectors (identical to Python)', () => {
  it.each(V.names.map((c) => [c] as [Case]))('name %#', (c: Case) => expect(refineName(c.input)).toEqual(c.expected));
  it.each(V.phones.map((c) => [c] as [Case]))('phone %#', (c: Case) => expect(refinePhone(c.input)).toEqual(c.expected));
  it.each(V.emails.map((c) => [c] as [Case]))('email %#', (c: Case) => expect(refineEmail(c.input)).toEqual(c.expected));
  it.each(V.dates.map((c) => [c] as [Case]))('date %#', (c: Case) => expect(parseDate(c.input, V.today)).toEqual(c.expected));
  it.each(V.tris.map((c) => [c] as [Case]))('tri %#', (c: Case) => {
    if (c.error) expect(() => parseTri(c.input)).toThrow();
    else expect(parseTri(c.input)).toEqual(c.expected);
  });
  it.each(V.rows.map((c) => [c] as [Case]))('row %#', (c: Case) => expect(refineRow(c.input, V.today)).toEqual(c.expected));
  it('has the required owner vectors', () => {
    expect(refineName('john doe md').value).toBe('John Doe, M.D.');
    expect(refineName('center for aba therapy of ny inc').value).toBe('Center for ABA Therapy of NY, Inc.');
  });
});

describe('performance', () => {
  it('refines 1,000 rows well under 1.5 s', () => {
    const rows = Array.from({ length: 1000 }, (_, i) => ({ name: `dr john doe${i} md`, phone: `(212) 555-${String(1000 + i).slice(-4)}`, email: `John${i}@Gmial.com`, called: 'Yes', connected: i % 2 ? 'Yes' : 'No', business_date: '10/14/2026' }));
    const t0 = performance.now();
    for (const r of rows) refineRow(r, '2026-10-20');
    const ms = performance.now() - t0;
    console.log(`refined 1000 rows in ${ms.toFixed(1)} ms`);
    expect(ms).toBeLessThan(1500);
  });
});

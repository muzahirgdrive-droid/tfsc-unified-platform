// The app bundles the shared engine through the `@tfsc/refine` alias; run the shared vectors through
// that exact import so a resolution or bundling drift shows up here, not in production.
import { describe, expect, it } from 'vitest';
import { refineName, refinePhone, refineRow } from '@tfsc/refine';
import vectors from '../../../packages/refine/vectors.json';

type Case = { input: never; expected?: unknown };
const V = vectors as unknown as { today: string; names: Case[]; phones: Case[]; rows: Case[] };

describe('@tfsc/refine via the app alias', () => {
  it('matches every shared name, phone and row vector', () => {
    for (const c of V.names) expect(refineName(c.input)).toEqual(c.expected);
    for (const c of V.phones) expect(refinePhone(c.input)).toEqual(c.expected);
    for (const c of V.rows) expect(refineRow(c.input, V.today)).toEqual(c.expected);
  });
});

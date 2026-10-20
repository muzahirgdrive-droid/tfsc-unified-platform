import { describe, expect, it } from 'vitest';
import { countdown, ptToUtc } from '@/lib/time';

describe('ptToUtc (Pacific wall clock → instant)', () => {
  it('handles PDT and PST', () => {
    expect(ptToUtc('2026-07-01', '10:00').toISOString()).toBe('2026-07-01T17:00:00.000Z');
    expect(ptToUtc('2026-12-01', '10:00').toISOString()).toBe('2026-12-01T18:00:00.000Z');
  });
  it('moves a spring-forward gap time to the next valid hour', () => {
    expect(ptToUtc('2026-03-08', '02:30').toISOString()).toBe('2026-03-08T10:30:00.000Z');
  });
  it('takes the first occurrence in the fall-back overlap', () => {
    expect(ptToUtc('2026-11-01', '01:30').toISOString()).toBe('2026-11-01T08:30:00.000Z');
  });
});

describe('countdown', () => {
  it('formats m:ss and never goes negative', () => {
    expect(countdown(3599)).toBe('59:59');
    expect(countdown(61.9)).toBe('1:01');
    expect(countdown(-5)).toBe('0:00');
  });
});

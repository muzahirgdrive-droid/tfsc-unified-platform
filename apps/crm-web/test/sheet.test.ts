import { describe, expect, it } from 'vitest';
import { COLUMNS, mapHeader, parseTable, toRows } from '@/lib/sheet';

describe('parseTable', () => {
  it('parses Excel/Sheets TSV with quoted multi-line cells', () => {
    const t = parseTable('Alpha Clinic\t(415) 555-0101\t"line one\nline ""two"""\r\nBeta\t415.555.0102\t\n');
    expect(t).toEqual([['Alpha Clinic', '(415) 555-0101', 'line one\nline "two"'], ['Beta', '415.555.0102', '']]);
  });
  it('parses CSV when there are no tabs', () => {
    expect(parseTable('a,"b, c",d\n1,2,3')).toEqual([['a', 'b, c', 'd'], ['1', '2', '3']]);
  });
  it('drops blank rows', () => {
    expect(parseTable('x\t1\n\t\n\ny\t2')).toEqual([['x', '1'], ['y', '2']]);
  });
});

describe('header detection', () => {
  it('maps workbook headers by alias, case/space-insensitive', () => {
    expect(mapHeader(['Provider  Name', 'PHONE', 'Remarks', 'whatever'])).toEqual(['name', 'phone', 'remarks', null]);
  });
  it('needs at least two recognised headers', () => {
    expect(mapHeader(['Alpha Clinic', '(415) 555-0101'])).toBeNull();
  });
  it('maps rows with a detected header (any column order)', () => {
    const { rows, headerDetected } = toRows([['Phone', 'Provider Name'], ['4155550101', 'Alpha']]);
    expect(headerDetected).toBe(true);
    expect(rows[0]).toMatchObject({ name: 'Alpha', phone: '4155550101', email: '' });
  });
  it('falls back to positional columns from the paste anchor', () => {
    const { rows, headerDetected } = toRows([['Alpha', '4155550101']], 1);
    expect(headerDetected).toBe(false);
    expect(rows[0]).toMatchObject({ name: 'Alpha', phone: '4155550101' });
  });
  it('covers workbook columns A–O exactly once', () => {
    expect(COLUMNS).toHaveLength(15);
    expect(new Set(COLUMNS.map((c) => c.key)).size).toBe(15);
  });
});

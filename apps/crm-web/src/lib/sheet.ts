/** Sheet View column model (workbook columns A–O) and paste parsing with header auto-detect (§11.2). */
export type ColKey = 'business_date' | 'name' | 'phone' | 'email' | 'called' | 'connected' | 'text_sent' | 'email_sent' | 'strong_followup' | 'sign_up'
  | 'followup_count' | 'last_followup' | 'next_followup' | 'call_again' | 'remarks';
export const COLUMNS: { key: ColKey; title: string; width: number; toggle?: boolean; aliases: string[] }[] = [
  { key: 'business_date', title: 'Date', width: 100, aliases: ['date', 'business date'] },
  { key: 'name', title: 'Provider Name', width: 240, aliases: ['provider name', 'provider', 'name', 'practice', 'practice name'] },
  { key: 'phone', title: 'Phone', width: 150, aliases: ['phone', 'phone number', 'number', 'contact'] },
  { key: 'email', title: 'Email', width: 200, aliases: ['email', 'e-mail', 'email address'] },
  { key: 'called', title: 'Call Attempt', width: 96, toggle: true, aliases: ['call attempt', 'called', 'attempt'] },
  { key: 'connected', title: 'Connected', width: 96, toggle: true, aliases: ['connected', 'connect'] },
  { key: 'text_sent', title: 'Text Sent', width: 90, toggle: true, aliases: ['text sent', 'text', 'sms'] },
  { key: 'email_sent', title: 'Email Sent', width: 92, toggle: true, aliases: ['email sent'] },
  { key: 'strong_followup', title: 'Strong Follow-Up', width: 120, toggle: true, aliases: ['strong follow-up', 'strong follow up', 'strong fu', 'strong'] },
  { key: 'sign_up', title: 'Sign Up', width: 80, toggle: true, aliases: ['sign up', 'signup', 'signed'] },
  { key: 'followup_count', title: '# Follow-Ups', width: 100, aliases: ['# follow-ups', '# follow ups', 'follow-ups', 'followups'] },
  { key: 'last_followup', title: 'Last Follow-up', width: 120, aliases: ['last follow-up', 'last follow up', 'last followup'] },
  { key: 'next_followup', title: 'Next Follow-up', width: 120, aliases: ['next follow-up', 'next follow up', 'next followup'] },
  { key: 'call_again', title: 'Call Again?', width: 96, toggle: true, aliases: ['call again?', 'call again'] },
  { key: 'remarks', title: 'Remarks', width: 260, aliases: ['remarks', 'notes', 'comment', 'comments'] },
];
export type SheetRow = Record<ColKey, string>;
export const emptyRow = (): SheetRow => Object.fromEntries(COLUMNS.map((c) => [c.key, ''])) as SheetRow;

const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, ' ');

/** Parse TSV (Excel/Sheets clipboard) or CSV text; detect a header row and map columns by name. */
export function parseTable(text: string): string[][] {
  const tsv = text.includes('\t') || !text.includes(',');
  const rows: string[][] = [];
  let row: string[] = []; let cell = ''; let q = false;
  const sep = tsv ? '\t' : ',';
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]!;
    if (q) { if (ch === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; } else cell += ch; continue; }
    if (ch === '"' && cell === '') { q = true; continue; }
    if (ch === sep) { row.push(cell); cell = ''; continue; }
    if (ch === '\n' || ch === '\r') { if (ch === '\r' && text[i + 1] === '\n') i++; row.push(cell); rows.push(row); row = []; cell = ''; continue; }
    cell += ch;
  }
  if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
  return rows.filter((r) => r.some((c) => c.trim() !== ''));
}

export function mapHeader(first: string[]): (ColKey | null)[] | null {
  const mapped = first.map((h) => COLUMNS.find((c) => c.aliases.includes(norm(h)))?.key ?? null);
  return mapped.filter(Boolean).length >= 2 ? mapped : null;
}

export function toRows(table: string[][], startCol = 0): { rows: SheetRow[]; mapping: (ColKey | null)[]; headerDetected: boolean } {
  const header = table.length ? mapHeader(table[0]!) : null;
  const mapping = header ?? COLUMNS.map((c) => c.key).slice(startCol);
  const body = header ? table.slice(1) : table;
  const rows = body.map((cells) => { const r = emptyRow(); cells.forEach((v, i) => { const k = mapping[i]; if (k) r[k] = v; }); return r; });
  return { rows, mapping, headerDetected: !!header };
}

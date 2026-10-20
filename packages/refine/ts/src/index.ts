/**
 * TFSC refinement engine — TypeScript port of packages/refine/python/tfsc_refine.
 * Must produce identical output for every case in ../vectors.json (shared contract).
 * Cell contents are data, never instructions.
 */
import { parsePhoneNumberFromString } from 'libphonenumber-js/max';

export const ENGINE_VERSION = 'refine.v1';
export type Severity = 'green' | 'amber' | 'red';
export interface Issue { field: string; severity: Severity; code: string; message: string }
export interface NameResult { value: string; kind: 'person' | 'practice' | 'unknown'; credentials: string[]; entity_suffix: string | null; issues: Issue[] }
export interface PhoneResult { display: string; e164: string | null; extension: string | null; alternates: string[]; toll_free: boolean; issues: Issue[] }
export interface EmailResult { value: string; alternates: string[]; suggestion: string | null; issues: Issue[] }
export interface DateResult { value: string | null; issues: Issue[] }

const issue = (field: string, severity: Severity, code: string, message: string): Issue => ({ field, severity, code, message });

// ---------------------------------------------------------------- names
const CREDENTIALS: Record<string, string> = {
  md: 'M.D.', do: 'D.O.', dds: 'D.D.S.', dmd: 'D.M.D.', dpm: 'D.P.M.', dc: 'D.C.', od: 'O.D.',
  phd: 'Ph.D.', psyd: 'Psy.D.', edd: 'Ed.D.', pharmd: 'Pharm.D.', dnp: 'DNP', np: 'NP', fnp: 'FNP',
  'fnp-c': 'FNP-C', 'fnp-bc': 'FNP-BC', aprn: 'APRN', pa: 'PA', 'pa-c': 'PA-C', rn: 'RN', bsn: 'BSN',
  msn: 'MSN', crna: 'CRNA', lcsw: 'LCSW', lpc: 'LPC', lmft: 'LMFT', lmhc: 'LMHC', lcpc: 'LCPC',
  bcba: 'BCBA', 'bcba-d': 'BCBA-D', dpt: 'DPT', pt: 'PT', 'otr/l': 'OTR/L', 'ccc-slp': 'CCC-SLP',
  facs: 'FACS', faap: 'FAAP', facp: 'FACP', facc: 'FACC', mph: 'MPH', mba: 'MBA',
};
const DOCTORAL = new Set(['md', 'do', 'dds', 'dmd', 'dpm', 'dc', 'od', 'phd', 'psyd', 'edd', 'pharmd', 'dnp', 'dpt']);
const ENTITY_SUFFIXES: Record<string, string> = {
  llc: 'LLC', pllc: 'PLLC', inc: 'Inc.', incorporated: 'Inc.', corp: 'Corp.', corporation: 'Corp.',
  co: 'Co.', ltd: 'Ltd.', pc: 'P.C.', pa: 'P.A.', llp: 'LLP', lp: 'LP', sc: 'S.C.', ps: 'P.S.',
};
const GENERATIONAL: Record<string, string> = { jr: 'Jr.', sr: 'Sr.', ii: 'II', iii: 'III', iv: 'IV' };
const ACRONYMS: Record<string, string> = Object.fromEntries([
  'ABA', 'ENT', 'OB/GYN', 'OBGYN', 'PT', 'OT', 'ST', 'MRI', 'ICU', 'ER', 'USA', 'NY', 'NJ', 'LA', 'DC', 'HIV',
  'ADHD', 'IV', 'MSO', 'IPA', 'ACO', 'RCM', 'II', 'III', 'LLC', 'PLLC', 'LLP', 'LP', 'USA', 'US',
].map((a) => [a.toLowerCase(), a]));
const MINOR_WORDS = new Set(['of', 'and', 'the', 'for', 'at', 'in', 'on', 'by']);
const PARTICLES = new Set(['de', 'la', 'del', 'della', 'van', 'der', 'von', 'di', 'da', 'du', 'le', 'dos', 'das', 'den', 'ter', 'bin', 'al']);
const PRACTICE_ONLY_ACRONYMS = new Set(['la', 'dc', 'pt', 'ot', 'st', 'er', 'iv', 'us']);
const PRACTICE_KEYWORDS = new Set([
  'health', 'healthcare', 'clinic', 'clinics', 'medical', 'medicine', 'center', 'centre', 'centers', 'group',
  'associates', 'practice', 'wellness', 'therapy', 'therapies', 'behavioral', 'pediatrics', 'pediatric', 'care',
  'services', 'service', 'hospital', 'institute', 'partners', 'solutions', 'rehab', 'rehabilitation', 'dental',
  'dentistry', 'family', 'chiropractic', 'imaging', 'labs', 'lab', 'laboratory', 'pharmacy', 'counseling',
  'psychiatry', 'psychiatric', 'orthopedics', 'orthopaedics', 'surgery', 'surgical', 'urgent', 'hospice',
  'cardiology', 'dermatology', 'neurology', 'oncology', 'radiology', 'urology', 'podiatry', 'optometry',
  'physicians', 'specialists', 'consultants', 'network', 'llc', 'pllc', 'inc', 'corp', 'spine', 'ortho',
  'diagnostics', 'home', 'living', 'recovery', 'treatment', 'counselling', 'mental', 'agency', 'office', 'offices',
  'billing', 'management', 'foundation', 'university', 'county', 'community', 'aba', 'mso', 'ipa', 'aco', 'rcm',
]);
const MAC_NAMES = new Set(['macdonald', 'macarthur', 'mackenzie', 'macintosh', 'maclean', 'macleod', 'macmillan', 'macneil',
  'macpherson', 'macgregor', 'macaulay', 'macdougall', 'macfarlane', 'mackinnon', 'maclaren']);
const PREFIX_TITLES = new Set(['dr', 'doctor']);

// Python-compatible character helpers (str.isalpha / isupper / islower).
const isAlpha = (ch: string) => /\p{L}/u.test(ch);
const isUpper = (ch: string) => ch !== ch.toLowerCase() && ch === ch.toUpperCase();
const isLower = (ch: string) => ch !== ch.toUpperCase() && ch === ch.toLowerCase();
const strIsLower = (s: string) => { const cased = [...s].filter((c) => isUpper(c) || isLower(c)); return cased.length > 0 && cased.every(isLower); };
const strip = (s: string, chars: string) => { let a = 0; let b = s.length; while (a < b && chars.includes(s[a]!)) a++; while (b > a && chars.includes(s[b - 1]!)) b--; return s.slice(a, b); };
const rstrip = (s: string, chars: string) => { let b = s.length; while (b > 0 && chars.includes(s[b - 1]!)) b--; return s.slice(0, b); };
const key = (tok: string) => strip(tok.toLowerCase().replaceAll('.', ''), ',;:');
const cap = (w: string) => (w ? [...w][0]!.toUpperCase() + [...w].slice(1).join('').toLowerCase() : w);

function caseWord(raw: string, index: number, kind: string, mixed: boolean): string {
  const k = rstrip(raw.toLowerCase(), '.');
  if (raw === '&' || raw === '+' || raw === '-') return raw;
  if (k === 'st' && (index === 0 || raw.endsWith('.'))) return 'St.';
  if (kind === 'person' && index > 0 && strIsLower(raw) && PARTICLES.has(k)) return raw;
  if (k in ACRONYMS && (kind === 'practice' || !PRACTICE_ONLY_ACRONYMS.has(k))) return ACRONYMS[k]!;
  if (kind === 'person' && [...rstrip(raw, '.')].length === 1 && isAlpha([...raw][0]!)) return [...raw][0]!.toUpperCase() + '.';
  if (index > 0 && MINOR_WORDS.has(k) && !raw.endsWith('.')) return k;
  if (mixed) {
    const chars = [...raw];
    const hasUpper = chars.some(isUpper);
    const hasLower = chars.some(isLower);
    if (hasUpper && hasLower && raw !== cap(raw)) return raw;
    const alphaCount = chars.filter(isAlpha).length;
    if (hasUpper && !hasLower && alphaCount >= 2 && alphaCount <= 4 && kind !== 'person') return raw;
  }
  if (raw.includes('-') && raw.length > 1) {
    return raw.split('-').map((p) => (p ? caseWord(p, index ? 1 : 0, kind, mixed) : p)).join('-');
  }
  const lower = raw.toLowerCase();
  if (/^[a-z]'[a-z].*$/s.test(lower)) return lower[0]!.toUpperCase() + "'" + cap(lower.slice(2));
  if (lower.startsWith('mc') && lower.length > 3 && [...lower.slice(2)].every(isAlpha)) return 'Mc' + cap(lower.slice(2));
  if (MAC_NAMES.has(lower)) return 'Mac' + cap(lower.slice(3));
  if (/^[a-z]\.?$/.test(lower)) return lower[0]!.toUpperCase() + (kind === 'person' ? '.' : raw.endsWith('.') ? '.' : '');
  if (/^\d/.test(lower)) return lower;
  return cap(lower);
}

export function refineName(rawIn: unknown, commaSuffix = true, splitOk = true): NameResult {
  const issues: Issue[] = [];
  let text = rawIn === null || rawIn === undefined ? '' : String(rawIn);
  text = text.replace(/[ \t\r\n]+/g, ' ');
  text = text.replace(/\s+/g, ' ').trim();
  text = strip(text, '"\'`“”‘’ ');
  text = text.replace(/[,;:]+$/, '').trim();
  text = text.replace(/\s*,\s*/g, ', ');
  text = text.replace(/,\s*,/g, ',');
  if (!text) return { value: '', kind: 'unknown', credentials: [], entity_suffix: null, issues: [issue('name', 'red', 'required', 'Provider or practice name is required.')] };
  if ([...text].some((ch) => ch.charCodeAt(0) < 32)) text = [...text].filter((ch) => ch.charCodeAt(0) >= 32).join('');
  const lowered = text.toLowerCase();
  let seps = [...text.matchAll(/\s*;\s*|\s+\/\s+|(?<=\S)\/(?=\S)|\s+(?:and|&)\s+(?=dr\.?\s)/gi)];
  seps = seps.filter((m) => m[0] !== '/' || !/(?:ob\/gyn|otr\/l)/.test(lowered.slice(Math.max(0, m.index! - 3), m.index! + m[0].length + 3)));
  if (seps.length && splitOk) {
    const parts: string[] = [];
    let last = 0;
    for (const m of seps) { parts.push(text.slice(last, m.index)); parts.push(m[0]); last = m.index! + m[0].length; }
    parts.push(text.slice(last));
    const out: string[] = [];
    let first: NameResult | null = null;
    parts.forEach((part, i) => {
      if (i % 2) {
        const sep = part.trim();
        out.push(sep === ';' ? '; ' : sep === '/' ? ' / ' : ` ${sep.toLowerCase()} `);
      } else {
        const r = refineName(part, commaSuffix, false);
        first = first ?? r;
        out.push(r.value);
      }
    });
    issues.push(issue('name', 'amber', 'split', 'This cell may contain two providers/practices. Split it?'));
    const f = first as NameResult | null;
    return { value: out.join(''), kind: f ? f.kind : 'unknown', credentials: f ? f.credentials : [], entity_suffix: f ? f.entity_suffix : null, issues: [...issues, ...(f ? f.issues : [])] };
  }
  const firstComma = text.indexOf(',');
  let tokens: Array<[string, boolean]> = [...text.matchAll(/[^\s,]+/g)].map((m) => [m[0], firstComma !== -1 && m.index! > firstComma]);
  let hasDr = false;
  if (tokens.length > 1 && PREFIX_TITLES.has(key(tokens[0]![0]))) { hasDr = true; tokens = tokens.slice(1); }

  const creds: string[] = [];
  const credKeys: string[] = [];
  let suffix: string | null = null;
  let generational: string | null = null;
  const unknownCreds: string[] = [];
  let ambiguousPa = false;
  while (tokens.length > 1) {
    const [tok, afterComma] = tokens[tokens.length - 1]!;
    const k = key(tok);
    if (k === 'pa' && suffix === null && creds.length === 0) { ambiguousPa = true; tokens.pop(); continue; }
    if (k in CREDENTIALS && suffix === null) {
      const prev = key(tokens[tokens.length - 2]![0]);
      const inPractice = tokens.slice(0, -1).some(([t]) => PRACTICE_KEYWORDS.has(key(t)));
      if (PRACTICE_ONLY_ACRONYMS.has(k) && !afterComma && (inPractice || MINOR_WORDS.has(prev))) break;
      creds.unshift(CREDENTIALS[k]!); credKeys.unshift(k); tokens.pop(); continue;
    }
    if (k in ENTITY_SUFFIXES && k !== 'pa' && suffix === null && creds.length === 0 && generational === null) {
      if (k === 'co' && !afterComma && !(tokens.length > 1 && ['&', 'and'].includes(tokens[tokens.length - 2]![0]))) break;
      suffix = ENTITY_SUFFIXES[k]!; tokens.pop(); continue;
    }
    if (k in GENERATIONAL && generational === null && suffix === null) { generational = GENERATIONAL[k]!; tokens.pop(); continue; }
    if (afterComma && /^[A-Za-z]{2,3}\.?$/.test(tok) && !MINOR_WORDS.has(k) && suffix === null) { unknownCreds.unshift(k.toUpperCase()); tokens.pop(); continue; }
    break;
  }

  const commaAtIdx = tokens.findIndex(([, ac]) => ac);
  const commaAt = commaAtIdx === -1 ? null : commaAtIdx;
  const body = tokens.map(([t]) => t);
  // Case style is judged on the name body only, ignoring known acronyms (peeled credentials,
  // suffixes and acronym promotion would otherwise skew it and break idempotence).
  const segments = body.flatMap((t) => t.split('-'));
  const letters = segments.filter((t) => !(rstrip(t.toLowerCase(), '.') in ACRONYMS) && [...rstrip(t, '.')].length > 1).flatMap((t) => [...t]).filter(isAlpha);
  const mixed = letters.length > 0 && !(letters.every(isUpper) || letters.every(isLower));
  const practice = suffix !== null || body.some((t) => PRACTICE_KEYWORDS.has(key(t)));
  if (ambiguousPa) {
    if (practice) suffix = suffix ?? ENTITY_SUFFIXES.pa!;
    else { creds.push(CREDENTIALS.pa!); credKeys.push('pa'); }
  }
  let kind: NameResult['kind'];
  if (practice) kind = 'practice';
  else if (hasDr || creds.length || unknownCreds.length || generational || (body.length >= 2 && body.length <= 4 && body.every((b) => isAlpha([...b][0]!) && [...b].every((ch) => isAlpha(ch) || ".'-".includes(ch))))) kind = 'person';
  else kind = 'unknown';

  const words = body.map((t, i) => caseWord(t, i, kind, mixed));
  if (commaAt !== null && commaAt > 0 && commaAt < words.length) {
    words[commaAt - 1] += ',';
    if (kind === 'person') issues.push(issue('name', 'amber', 'name_order', 'Looks like "Last, First". Kept as typed; please confirm.'));
  }
  let name = words.join(' ').replaceAll(' ,', ',');
  name = strip(name.replace(/\s*,\s*/g, ', '), ', ');
  if (generational) name = `${name} ${generational}`;
  if (hasDr && !credKeys.some((k) => DOCTORAL.has(k))) name = 'Dr. ' + name;
  const tail = [...creds, ...unknownCreds];
  if (tail.length) name = name + ', ' + tail.join(', ');
  if (suffix) {
    const joiner = !commaSuffix || name.endsWith(' &') || name.endsWith(' and') || name.endsWith(' And') ? ' ' : ', ';
    name = name + joiner + suffix;
  }
  for (const u of unknownCreds) issues.push(issue('name', 'amber', 'unrecognized_credential', `Unrecognized credential "${u}" kept as typed. Please confirm.`));
  if ([...name].length > 200) issues.push(issue('name', 'red', 'too_long', 'Name is longer than 200 characters.'));
  return { value: name, kind, credentials: tail, entity_suffix: suffix, issues };
}

// ---------------------------------------------------------------- phones
const EXT = /(?:\s*(?:ext\.?|extension|ex\.?|x|#)\s*(\d{1,6}))\s*$/i;
const TOLL_FREE = new Set(['800', '833', '844', '855', '866', '877', '888']);

function phoneText(raw: unknown): string {
  if (typeof raw === 'number') return Number.isInteger(raw) ? String(raw) : String(raw);
  let s = raw === null || raw === undefined ? '' : String(raw);
  s = s.trim();
  const m = /^(\d{10,11})\.0+$/.exec(s);
  return m ? m[1]! : s;
}

function parseOne(textIn: string): [string, string | null, string | null, boolean, Issue[]] {
  const issues: Issue[] = [];
  let text = textIn;
  let ext: string | null = null;
  const m = EXT.exec(text);
  if (m) { ext = m[1]!; text = text.slice(0, m.index); }
  let digits = text.replace(/\D/g, '');
  const hasPlus = text.trim().startsWith('+');
  if (hasPlus && !digits.startsWith('1')) return [text.trim(), null, ext, false, [issue('phone', 'red', 'non_nanp', 'Only U.S./North American numbers are supported. Row kept as research until fixed.')]];
  if (digits.length === 11 && digits.startsWith('1')) digits = digits.slice(1);
  if (digits.length !== 10) return [text.trim(), null, ext, false, [issue('phone', 'red', 'invalid', 'Phone must have 10 digits (U.S.).')]];
  if ('01'.includes(digits[0]!)) return [text.trim(), null, ext, false, [issue('phone', 'red', 'impossible', 'A U.S. area code cannot start with 0 or 1.')]];
  const display = `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
  const tollFree = TOLL_FREE.has(digits.slice(0, 3));
  const parsed = parsePhoneNumberFromString('+1' + digits);
  if (!tollFree && !(parsed && parsed.isValid())) issues.push(issue('phone', 'amber', 'unverified', 'This number does not match an assigned U.S. area code/exchange. Please double-check.'));
  if (tollFree) issues.push(issue('phone', 'green', 'toll_free', 'Toll-free number.'));
  return [display + (ext ? ` ext. ${ext}` : ''), '+1' + digits, ext, tollFree, issues];
}

export function refinePhone(raw: unknown): PhoneResult {
  let text = phoneText(raw);
  if (!text) return { display: '', e164: null, extension: null, alternates: [], toll_free: false, issues: [] };
  if ([...text].some((ch) => ch.charCodeAt(0) < 32 && !'\n\r\t'.includes(ch))) text = [...text].filter((ch) => ch.charCodeAt(0) >= 32 || '\n\r\t'.includes(ch)).join('');
  let parts = text.split(/\s*(?:\/|;|,|\||\bor\b|\n|&)\s*/i).filter((p) => p && /\d/.test(p));
  const long = parts.filter((p) => p.replace(/\D/g, '').length >= 7);
  if (long.length) parts = long;
  if (parts.length <= 1) {
    const [display, e164, ext, tf, issues] = parseOne(text);
    return { display, e164, extension: ext, alternates: [], toll_free: tf, issues };
  }
  const [display, e164, ext, tf, issues] = parseOne(parts[0]!);
  const alternates = parts.slice(1).map((p) => { const [d2, e2] = parseOne(p); return e2 ? d2 : p.trim(); });
  issues.push(issue('phone', 'amber', 'multiple', `${parts.length} numbers found; the first is primary and the rest are kept as alternates.`));
  return { display, e164, extension: ext, alternates, toll_free: tf, issues };
}

// ---------------------------------------------------------------- emails
const EMAIL = /^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,24}$/;
const DOMAIN_TYPOS: Record<string, string> = {
  'gmial.com': 'gmail.com', 'gmal.com': 'gmail.com', 'gmai.com': 'gmail.com', 'gamil.com': 'gmail.com',
  'gmail.co': 'gmail.com', 'gmail.con': 'gmail.com', 'gmail.cm': 'gmail.com', 'gmaill.com': 'gmail.com', 'gnail.com': 'gmail.com',
  'yaho.com': 'yahoo.com', 'yahooo.com': 'yahoo.com', 'yahoo.co': 'yahoo.com', 'yahoo.con': 'yahoo.com', 'yhoo.com': 'yahoo.com',
  'hotmial.com': 'hotmail.com', 'hotmal.com': 'hotmail.com', 'hotmail.co': 'hotmail.com', 'hotmail.con': 'hotmail.com', 'hotmai.com': 'hotmail.com',
  'outlok.com': 'outlook.com', 'outloo.com': 'outlook.com', 'outlook.co': 'outlook.com', 'outlook.con': 'outlook.com',
  'aol.con': 'aol.com', 'icloud.con': 'icloud.com', 'iclod.com': 'icloud.com',
};
const TLD_TYPOS: Record<string, string> = { con: 'com', cmo: 'com', ocm: 'com', comm: 'com', vom: 'com', xom: 'com', cpm: 'com' };

function cleanEmail(sIn: string): string {
  let s = sIn.trim();
  const m = /<([^<>]*)>/.exec(s);
  if (m) s = m[1]!;
  s = s.replace(/^\s*mailto:/i, '');
  s = s.replace(/\s+/g, '');
  s = strip(s, '<>"\'');
  s = s.replace(/[.,;:]+$/, '');
  return s.toLowerCase();
}

function suggest(addr: string): string | null {
  let s = addr.replace(/@{2,}/g, '@');
  if ((s.match(/@/g) ?? []).length === 1) {
    const [local, domIn] = s.split('@') as [string, string];
    let dom = domIn.replaceAll(',', '.').replace(/\.{2,}/g, '.');
    if (dom in DOMAIN_TYPOS) dom = DOMAIN_TYPOS[dom]!;
    else {
      const i = dom.lastIndexOf('.');
      if (i !== -1 && dom.slice(i + 1) in TLD_TYPOS) {
        dom = dom.slice(0, i) + '.' + TLD_TYPOS[dom.slice(i + 1)];
        dom = DOMAIN_TYPOS[dom] ?? dom;
      }
    }
    s = local + '@' + dom;
  }
  return s !== addr ? s : null;
}

export function refineEmail(raw: unknown): EmailResult {
  const text = (raw === null || raw === undefined ? '' : String(raw)).trim();
  if (!text) return { value: '', alternates: [], suggestion: null, issues: [] };
  const atCount = (text.match(/@/g) ?? []).length;
  const pieces = atCount > 1 && !/@\s*@/.test(text) ? text.split(/[;,/\s]+(?=[^;,/\s]*@)/).filter((p) => p.trim()) : [text];
  if (pieces.length > 1 && pieces.every((p) => p.includes('@'))) {
    const cleaned = pieces.map(cleanEmail).filter(Boolean);
    const primary = refineEmail(cleaned[0]);
    primary.alternates = cleaned.slice(1);
    primary.issues.push(issue('email', 'amber', 'multiple', `${cleaned.length} addresses found; the first is primary and the rest are kept as alternates.`));
    return primary;
  }
  const value = cleanEmail(text);
  const issues: Issue[] = [];
  const suggestion = suggest(value);
  const valid = EMAIL.test(value) && !value.includes('..');
  if (suggestion) issues.push(issue('email', 'amber', 'typo', `Did you mean ${suggestion}?`));
  if (!valid) {
    if (suggestion && EMAIL.test(suggestion)) issues.push(issue('email', 'red', 'invalid', `Email is not valid as typed. Accept the suggestion ${suggestion} to fix it.`));
    else issues.push(issue('email', 'red', 'invalid', 'Email address is not valid.'));
  }
  return { value, alternates: [], suggestion, issues };
}

// ---------------------------------------------------------------- dates
const MONTHS: Record<string, number> = Object.fromEntries(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'].map((m, i) => [m, i + 1]));
MONTHS.sept = 9;
const MON_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const pad = (n: number, w = 2) => String(n).padStart(w, '0');
function mk(y: number, m: number, d: number): { y: number; m: number; d: number } | null {
  if (m < 1 || m > 12 || d < 1 || y < 1 || y > 9999) return null;
  const dt = new Date(Date.UTC(2000, m - 1, d));
  dt.setUTCFullYear(y);
  return dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d ? { y, m, d } : null;
}
const iso = (x: { y: number; m: number; d: number }) => `${pad(x.y, 4)}-${pad(x.m)}-${pad(x.d)}`;

export function parseDate(raw: unknown, today: string, fieldName = 'business_date', defaultToday = true): DateResult {
  const tYear = Number(today.slice(0, 4));
  if (raw === null || raw === undefined || (typeof raw === 'string' && !raw.trim())) {
    return defaultToday ? { value: today, issues: [issue(fieldName, 'green', 'defaulted', 'Empty date set to today (Pacific).')] } : { value: null, issues: [] };
  }
  if (typeof raw === 'boolean') return { value: null, issues: [issue(fieldName, 'red', 'invalid', 'Date could not be read.')] };
  if (typeof raw === 'number' || (typeof raw === 'string' && /^\d{5}(?:\.0+)?$/.test(raw.trim()))) {
    const n = Number(raw);
    if (!Number.isInteger(n) || !(n >= 20000 && n <= 80000)) return { value: null, issues: [issue(fieldName, 'red', 'invalid', 'Date number is not a valid Excel date.')] };
    const dt = new Date(Date.UTC(1899, 11, 30) + n * 86400000);
    return { value: `${pad(dt.getUTCFullYear(), 4)}-${pad(dt.getUTCMonth() + 1)}-${pad(dt.getUTCDate())}`, issues: [] };
  }
  let s = String(raw).trim().replace(/\s+/g, ' ');
  s = s.replace(/[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:\s?[ap]m)?(?:Z|[+-]\d{2}:?\d{2})?$/i, '').trim();
  let m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(s);
  if (m) { const d = mk(+m[1]!, +m[2]!, +m[3]!); return d ? { value: iso(d), issues: [] } : { value: null, issues: [issue(fieldName, 'red', 'invalid', 'Date does not exist.')] }; }
  m = /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})$/.exec(s);
  if (m) {
    const a = +m[1]!; const b = +m[2]!; let y = +m[3]!;
    if (y < 100) y += 2000;
    if (a > 12 && b <= 12) {
      const d = mk(y, b, a);
      if (d) return { value: iso(d), issues: [issue(fieldName, 'amber', 'ambiguous', `Interpreted as ${MON_ABBR[d.m - 1]} ${d.d}, ${d.y} (day/month order).`)] };
    }
    const d = mk(y, a, b);
    return d ? { value: iso(d), issues: [] } : { value: null, issues: [issue(fieldName, 'red', 'invalid', 'Date does not exist.')] };
  }
  m = /^(\d{1,2})[\s-]([A-Za-z]{3,9})\.?[\s\-,]*(\d{2}|\d{4})?$/.exec(s);
  if (m && (rstrip(m[2]!.toLowerCase().slice(0, 4), 't') in MONTHS || m[2]!.toLowerCase().slice(0, 3) in MONTHS)) {
    const mon = MONTHS[m[2]!.toLowerCase().slice(0, 3)];
    let y = m[3] ? +m[3] : tYear;
    if (y < 100) y += 2000;
    const d = mk(y, mon ?? 0, +m[1]!);
    if (d) return { value: iso(d), issues: m[3] ? [] : [issue(fieldName, 'green', 'year_assumed', `Year ${y} assumed.`)] };
  }
  m = /^([A-Za-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)?,? ?(\d{4})?$/.exec(s);
  if (m && m[1]!.toLowerCase().slice(0, 3) in MONTHS) {
    const y = m[3] ? +m[3] : tYear;
    const d = mk(y, MONTHS[m[1]!.toLowerCase().slice(0, 3)]!, +m[2]!);
    if (d) return { value: iso(d), issues: m[3] ? [] : [issue(fieldName, 'green', 'year_assumed', `Year ${y} assumed.`)] };
  }
  return { value: null, issues: [issue(fieldName, 'red', 'invalid', 'Use MM/DD/YYYY, YYYY-MM-DD, an Excel date or a month name.')] };
}

// ---------------------------------------------------------------- toggles
const TRUE = new Set(['yes', 'y', 'true', 't', '1', 'x', '✓', '✔', 'done']);
const FALSE = new Set(['no', 'n', 'false', 'f', '0']);
const UNSET = new Set(['', 'unset', 'n/a', 'na', '-', '—']);

export function parseTri(raw: unknown): boolean | null {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === 'boolean') return raw;
  if (typeof raw === 'number' && (raw === 0 || raw === 1)) return raw === 1;
  const s = String(raw).trim().toLowerCase();
  if (TRUE.has(s)) return true;
  if (FALSE.has(s)) return false;
  if (UNSET.has(s)) return null;
  throw new Error(`"${String(raw)}" is not Yes, No or blank.`);
}

// ---------------------------------------------------------------- rows
export const ROW_TOGGLES = ['called', 'connected', 'text_sent', 'email_sent', 'followup_needed', 'strong_followup', 'sign_up', 'call_again'] as const;
const textCell = (raw: unknown, limit: number) => [...(raw === null || raw === undefined ? '' : String(raw))].filter((ch) => ch.charCodeAt(0) >= 32 || ch === '\n' || ch === '\t').join('').trim().slice(0, limit);

export interface RefinedRow { refined: Record<string, unknown>; issues: Issue[]; severity: Severity }

export function refineRow(row: Record<string, unknown>, today: string, commaSuffix = true): RefinedRow {
  const issues: Issue[] = [];
  const name = refineName(row.name ?? row.provider_name, commaSuffix); issues.push(...name.issues);
  const phone = refinePhone(row.phone); issues.push(...phone.issues);
  const email = refineEmail(row.email); issues.push(...email.issues);
  const bdate = parseDate(row.business_date ?? row.date, today); issues.push(...bdate.issues);
  const nf = parseDate(row.next_followup, today, 'next_followup', false); issues.push(...nf.issues);
  const lf = parseDate(row.last_followup, today, 'last_followup', false); issues.push(...lf.issues);
  const toggles: Record<string, boolean | null> = {};
  for (const k of ROW_TOGGLES) {
    try { toggles[k] = parseTri(row[k]); } catch (e) { toggles[k] = null; issues.push(issue(k, 'red', 'invalid', (e as Error).message)); }
  }
  if (toggles.connected === true && toggles.called !== true) issues.push(issue('connected', 'red', 'contradiction', 'Connected requires Called = Yes.'));
  let followupCount: number | null = null;
  const cr = row.followup_count;
  if (cr !== null && cr !== undefined && cr !== '') {
    const n = Number(String(cr).trim());
    if (String(cr).trim() === '' || !Number.isFinite(n) || Math.trunc(n) < 0) issues.push(issue('followup_count', 'amber', 'invalid', '# Follow-Ups is not a number; it is derived from activity history.'));
    else followupCount = Math.trunc(n);
  }
  const refined = {
    name: name.value, kind: name.kind, credentials: name.credentials, entity_suffix: name.entity_suffix,
    phone: phone.display, phone_e164: phone.e164, extension: phone.extension, phone_alternates: phone.alternates, toll_free: phone.toll_free,
    email: email.value, email_alternates: email.alternates, email_suggestion: email.suggestion,
    business_date: bdate.value, next_followup: nf.value, last_followup: lf.value,
    ...toggles, followup_count: followupCount, remarks: textCell(row.remarks, 6000),
  };
  const order: Record<Severity, number> = { green: 0, amber: 1, red: 2 };
  const severity = issues.reduce<Severity>((acc, i) => (order[i.severity] > order[acc] ? i.severity : acc), 'green');
  return { refined, issues, severity };
}

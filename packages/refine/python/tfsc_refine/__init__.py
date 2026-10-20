"""TFSC refinement engine (Python reference implementation).

The TypeScript port in ../../ts must produce identical output for every vector in
../../vectors.json. Cell contents are data, never instructions.

Severity levels: "green" = safe automatic fix, "amber" = suggestion needing review,
"red" = invalid (blocks saving that row only).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

import phonenumbers

__all__ = [
    'ENGINE_VERSION',
    'DateResult',
    'EmailResult',
    'Issue',
    'NameResult',
    'PhoneResult',
    'parse_date',
    'parse_tri',
    'refine_email',
    'refine_name',
    'refine_phone',
    'refine_row',
]

ENGINE_VERSION = 'refine.v1'


@dataclass
class Issue:
    field: str
    severity: str  # green | amber | red
    code: str
    message: str


@dataclass
class NameResult:
    value: str
    kind: str  # person | practice | unknown
    credentials: list[str] = field(default_factory=list)
    entity_suffix: str | None = None
    issues: list[Issue] = field(default_factory=list)


@dataclass
class PhoneResult:
    display: str
    e164: str | None
    extension: str | None = None
    alternates: list[str] = field(default_factory=list)
    toll_free: bool = False
    issues: list[Issue] = field(default_factory=list)


@dataclass
class EmailResult:
    value: str
    alternates: list[str] = field(default_factory=list)
    suggestion: str | None = None
    issues: list[Issue] = field(default_factory=list)


@dataclass
class DateResult:
    value: str | None
    issues: list[Issue] = field(default_factory=list)


# ---------------------------------------------------------------- names
CREDENTIALS: dict[str, str] = {
    'md': 'M.D.', 'do': 'D.O.', 'dds': 'D.D.S.', 'dmd': 'D.M.D.', 'dpm': 'D.P.M.', 'dc': 'D.C.', 'od': 'O.D.',
    'phd': 'Ph.D.', 'psyd': 'Psy.D.', 'edd': 'Ed.D.', 'pharmd': 'Pharm.D.', 'dnp': 'DNP', 'np': 'NP', 'fnp': 'FNP',
    'fnp-c': 'FNP-C', 'fnp-bc': 'FNP-BC', 'aprn': 'APRN', 'pa': 'PA', 'pa-c': 'PA-C', 'rn': 'RN', 'bsn': 'BSN',
    'msn': 'MSN', 'crna': 'CRNA', 'lcsw': 'LCSW', 'lpc': 'LPC', 'lmft': 'LMFT', 'lmhc': 'LMHC', 'lcpc': 'LCPC',
    'bcba': 'BCBA', 'bcba-d': 'BCBA-D', 'dpt': 'DPT', 'pt': 'PT', 'otr/l': 'OTR/L', 'ccc-slp': 'CCC-SLP',
    'facs': 'FACS', 'faap': 'FAAP', 'facp': 'FACP', 'facc': 'FACC', 'mph': 'MPH', 'mba': 'MBA',
}
DOCTORAL = {'md', 'do', 'dds', 'dmd', 'dpm', 'dc', 'od', 'phd', 'psyd', 'edd', 'pharmd', 'dnp', 'dpt'}
ENTITY_SUFFIXES: dict[str, str] = {
    'llc': 'LLC', 'pllc': 'PLLC', 'inc': 'Inc.', 'incorporated': 'Inc.', 'corp': 'Corp.', 'corporation': 'Corp.',
    'co': 'Co.', 'ltd': 'Ltd.', 'pc': 'P.C.', 'pa': 'P.A.', 'llp': 'LLP', 'lp': 'LP', 'sc': 'S.C.', 'ps': 'P.S.',
}
GENERATIONAL: dict[str, str] = {'jr': 'Jr.', 'sr': 'Sr.', 'ii': 'II', 'iii': 'III', 'iv': 'IV'}
ACRONYMS: dict[str, str] = {k.lower(): k for k in [
    'ABA', 'ENT', 'OB/GYN', 'OBGYN', 'PT', 'OT', 'ST', 'MRI', 'ICU', 'ER', 'USA', 'NY', 'NJ', 'LA', 'DC', 'HIV',
    'ADHD', 'IV', 'MSO', 'IPA', 'ACO', 'RCM', 'II', 'III', 'LLC', 'PLLC', 'LLP', 'LP', 'USA', 'US',
]}
MINOR_WORDS = {'of', 'and', 'the', 'for', 'at', 'in', 'on', 'by'}
PARTICLES = {'de', 'la', 'del', 'della', 'van', 'der', 'von', 'di', 'da', 'du', 'le', 'dos', 'das', 'den', 'ter', 'bin', 'al'}
PRACTICE_ONLY_ACRONYMS = {'la', 'dc', 'pt', 'ot', 'st', 'er', 'iv', 'us'}
PRACTICE_KEYWORDS = {
    'health', 'healthcare', 'clinic', 'clinics', 'medical', 'medicine', 'center', 'centre', 'centers', 'group',
    'associates', 'practice', 'wellness', 'therapy', 'therapies', 'behavioral', 'pediatrics', 'pediatric', 'care',
    'services', 'service', 'hospital', 'institute', 'partners', 'solutions', 'rehab', 'rehabilitation', 'dental',
    'dentistry', 'family', 'chiropractic', 'imaging', 'labs', 'lab', 'laboratory', 'pharmacy', 'counseling',
    'psychiatry', 'psychiatric', 'orthopedics', 'orthopaedics', 'surgery', 'surgical', 'urgent', 'hospice',
    'cardiology', 'dermatology', 'neurology', 'oncology', 'radiology', 'urology', 'podiatry', 'optometry',
    'physicians', 'specialists', 'consultants', 'network', 'llc', 'pllc', 'inc', 'corp', 'spine', 'ortho',
    'diagnostics', 'home', 'living', 'recovery', 'treatment', 'counselling', 'mental', 'agency', 'office', 'offices',
    'billing', 'management', 'foundation', 'university', 'county', 'community', 'aba', 'mso', 'ipa', 'aco', 'rcm',
}
MAC_NAMES = {'macdonald', 'macarthur', 'mackenzie', 'macintosh', 'maclean', 'macleod', 'macmillan', 'macneil',
             'macpherson', 'macgregor', 'macaulay', 'macdougall', 'macfarlane', 'mackinnon', 'maclaren'}
PREFIX_TITLES = {'dr': 'Dr.', 'doctor': 'Dr.'}
_SPLIT_SLASH_OK = {'ob/gyn', 'otr/l'}


def _key(tok: str) -> str:
    return tok.lower().replace('.', '').strip(',;:')


def _cap(word: str) -> str:
    return word[:1].upper() + word[1:].lower() if word else word


def _case_word(tok: str, index: int, kind: str, mixed_input: bool) -> str:
    raw = tok
    k = raw.lower().rstrip('.')
    if raw in ('&', '+', '-'):
        return raw
    if k == 'st' and (index == 0 or raw.endswith('.')):
        return 'St.'  # Saint
    if kind == 'person' and index > 0 and raw.islower() and k in PARTICLES:
        return raw  # de la Cruz, van der Berg: keep lowercase particles as typed
    if k in ACRONYMS and (kind == 'practice' or k not in PRACTICE_ONLY_ACRONYMS):
        return ACRONYMS[k]
    if kind == 'person' and len(raw.rstrip('.')) == 1 and raw[0].isalpha():
        return raw[0].upper() + '.'
    if index > 0 and k in MINOR_WORDS and not raw.endswith('.'):
        return k
    if mixed_input:
        has_upper = any(ch.isupper() for ch in raw)
        has_lower = any(ch.islower() for ch in raw)
        if has_upper and has_lower and raw != _cap(raw):
            return raw  # deliberate mixed case such as DeVito or iCare: never respell
        if has_upper and not has_lower and 2 <= sum(ch.isalpha() for ch in raw) <= 4 and kind != 'person':
            return raw  # short all-caps token in mixed input: treat as acronym
    if '-' in raw and len(raw) > 1:
        return '-'.join(_case_word(p, 1 if index else 0, kind, mixed_input) if p else p for p in raw.split('-'))
    lower = raw.lower()
    if re.fullmatch(r"[a-z]'[a-z].*", lower):  # o'neil, d'angelo
        return lower[0].upper() + "'" + _cap(lower[2:])
    if lower.startswith('mc') and len(lower) > 3 and lower[2:].isalpha():
        return 'Mc' + _cap(lower[2:])
    if lower in MAC_NAMES:
        return 'Mac' + _cap(lower[3:])
    if re.fullmatch(r'[a-z]\.?', lower):
        return lower[0].upper() + ('.' if kind == 'person' else ('.' if raw.endswith('.') else ''))
    if re.match(r'^\d', lower):
        return lower  # 24-7, 3rd, 1st
    return _cap(lower)


def refine_name(raw: Any, comma_suffix: bool = True, _split_ok: bool = True) -> NameResult:
    issues: list[Issue] = []
    text = '' if raw is None else str(raw)
    text = re.sub(r'[ \t\r\n]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.strip('"\'`“”‘’ ')
    text = re.sub(r'[,;:]+$', '', text).strip()
    text = re.sub(r'\s*,\s*', ', ', text)
    text = re.sub(r',\s*,', ',', text)
    if not text:
        return NameResult('', 'unknown', issues=[Issue('name', 'red', 'required', 'Provider or practice name is required.')])
    if any(ord(ch) < 32 for ch in text):
        text = ''.join(ch for ch in text if ord(ch) >= 32)
    # Two entities in one cell? Refine each part separately, never merge them.
    lowered = text.lower()
    seps = list(re.finditer(r'\s*;\s*|\s+/\s+|(?<=\S)/(?=\S)|\s+(?:and|&)\s+(?=dr\.?\s)', text, flags=re.I))
    seps = [m for m in seps if m.group(0) != '/' or not re.search(r'(?:ob/gyn|otr/l)', lowered[max(0, m.start() - 3):m.end() + 3])]
    if seps and _split_ok:
        parts, last = [], 0
        for m in seps:
            parts.append(text[last:m.start()]); parts.append(m.group(0)); last = m.end()
        parts.append(text[last:])
        out, first = [], None
        for i, part in enumerate(parts):
            if i % 2:
                sep = part.strip()
                out.append('; ' if sep == ';' else (' / ' if sep == '/' else f' {sep.lower()} '))
            else:
                r = refine_name(part, comma_suffix, _split_ok=False)
                first = first or r
                out.append(r.value)
        issues.append(Issue('name', 'amber', 'split', 'This cell may contain two providers/practices. Split it?'))
        return NameResult(''.join(out), first.kind if first else 'unknown', first.credentials if first else [], first.entity_suffix if first else None, issues + (first.issues if first else []))
    letters = [ch for ch in text if ch.isalpha()]
    mixed_input = bool(letters) and not (all(ch.isupper() for ch in letters) or all(ch.islower() for ch in letters))

    first_comma = text.find(',')
    tokens: list[tuple[str, bool]] = []  # (token, after_comma)
    for m in re.finditer(r'[^\s,]+', text):
        tokens.append((m.group(0), first_comma != -1 and m.start() > first_comma))

    has_dr = False
    if tokens and _key(tokens[0][0]) in PREFIX_TITLES and len(tokens) > 1:
        has_dr = True
        tokens = tokens[1:]

    creds: list[str] = []
    cred_keys: list[str] = []
    suffix: str | None = None
    generational: str | None = None
    unknown_creds: list[str] = []
    ambiguous_pa = False
    # Peel credentials / suffixes / generational tokens from the end.
    while len(tokens) > 1:
        tok, after_comma = tokens[-1]
        k = _key(tok)
        if k == 'pa' and suffix is None and not creds:
            ambiguous_pa = True
            tokens.pop()
            continue
        if k in CREDENTIALS and suffix is None:
            prev = _key(tokens[-2][0])
            in_practice = bool({_key(t) for t, _ in tokens[:-1]} & PRACTICE_KEYWORDS)
            if k in PRACTICE_ONLY_ACRONYMS and not after_comma and (in_practice or prev in MINOR_WORDS):
                break  # 'Imaging of DC', 'Sunrise PT': an acronym in a practice name, not a credential
            creds.insert(0, CREDENTIALS[k]); cred_keys.insert(0, k); tokens.pop(); continue
        if k in ENTITY_SUFFIXES and k != 'pa' and suffix is None and not creds and generational is None:
            if k == 'co' and not after_comma and not (len(tokens) > 1 and tokens[-2][0] in ('&', 'and')):
                break
            suffix = ENTITY_SUFFIXES[k]; tokens.pop(); continue
        if k in GENERATIONAL and generational is None and suffix is None:
            generational = GENERATIONAL[k]; tokens.pop(); continue
        if after_comma and re.fullmatch(r'[A-Za-z]{2,3}\.?', tok) and k not in MINOR_WORDS and suffix is None:
            unknown_creds.insert(0, k.upper()); tokens.pop(); continue
        break

    comma_at = next((i for i, (_t, ac) in enumerate(tokens) if ac), None)
    body = [t for t, _ in tokens]
    body_keys = {_key(t) for t in body}
    practice = bool(suffix) or bool(body_keys & PRACTICE_KEYWORDS)
    if ambiguous_pa:
        if practice:
            suffix = suffix or ENTITY_SUFFIXES['pa']
        else:
            creds.append(CREDENTIALS['pa']); cred_keys.append('pa')
    if practice:
        kind = 'practice'
    elif has_dr or creds or unknown_creds or generational or (2 <= len(body) <= 4 and all(b[0].isalpha() and all(ch.isalpha() or ch in ".'-" for ch in b) for b in body)):
        kind = 'person'
    else:
        kind = 'unknown'

    words = [_case_word(t, i, kind, mixed_input) for i, t in enumerate(body)]
    if comma_at is not None and 0 < comma_at < len(words):
        words[comma_at - 1] += ','  # keep the typed comma ('Doe, John'); never reorder names
        if kind == 'person':
            issues.append(Issue('name', 'amber', 'name_order', 'Looks like "Last, First". Kept as typed; please confirm.'))
    name = ' '.join(words).replace(' ,', ',')
    name = re.sub(r'\s*,\s*', ', ', name).strip(', ')
    if generational:
        name = f'{name} {generational}'
    if has_dr and not any(k in DOCTORAL for k in cred_keys):
        name = 'Dr. ' + name
    tail = creds + unknown_creds
    if tail:
        name = name + ', ' + ', '.join(tail)
    if suffix:
        joiner = ' ' if (not comma_suffix or name.endswith((' &', ' and', ' And'))) else ', '
        name = name + joiner + suffix
    for u in unknown_creds:
        issues.append(Issue('name', 'amber', 'unrecognized_credential', f'Unrecognized credential "{u}" kept as typed. Please confirm.'))
    if len(name) > 200:
        issues.append(Issue('name', 'red', 'too_long', 'Name is longer than 200 characters.'))
    return NameResult(name, kind, creds + unknown_creds, suffix, issues)


# ---------------------------------------------------------------- phones
_EXT = re.compile(r'(?:\s*(?:ext\.?|extension|ex\.?|x|#)\s*(\d{1,6}))\s*$', re.I)
_TOLL_FREE = {'800', '833', '844', '855', '866', '877', '888'}


def _phone_digits_text(raw: Any) -> str:
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    if isinstance(raw, int):
        return str(raw)
    s = '' if raw is None else str(raw)
    s = s.strip()
    m = re.fullmatch(r'(\d{10,11})\.0+', s)
    if m:
        return m.group(1)
    return s


def _parse_one(text: str) -> tuple[str, str | None, str | None, bool, list[Issue]]:
    issues: list[Issue] = []
    ext = None
    m = _EXT.search(text)
    if m:
        ext = m.group(1)
        text = text[:m.start()]
    digits = re.sub(r'\D', '', text)
    has_plus = text.strip().startswith('+')
    if has_plus and not digits.startswith('1'):
        return text.strip(), None, ext, False, [Issue('phone', 'red', 'non_nanp', 'Only U.S./North American numbers are supported. Row kept as research until fixed.')]
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    if len(digits) != 10:
        return text.strip(), None, ext, False, [Issue('phone', 'red', 'invalid', 'Phone must have 10 digits (U.S.).')]
    try:
        p = phonenumbers.parse('+1' + digits, None)
    except phonenumbers.NumberParseException:
        return text.strip(), None, ext, False, [Issue('phone', 'red', 'invalid', 'Phone could not be read as a U.S. number.')]
    if digits[0] in '01':
        return text.strip(), None, ext, False, [Issue('phone', 'red', 'impossible', 'A U.S. area code cannot start with 0 or 1.')]
    display = f'({digits[0:3]}) {digits[3:6]}-{digits[6:]}'
    toll_free = digits[:3] in _TOLL_FREE
    if not toll_free and not phonenumbers.is_valid_number(p):
        issues.append(Issue('phone', 'amber', 'unverified', 'This number does not match an assigned U.S. area code/exchange. Please double-check.'))
    if toll_free:
        issues.append(Issue('phone', 'green', 'toll_free', 'Toll-free number.'))
    return display + (f' ext. {ext}' if ext else ''), '+1' + digits, ext, toll_free, issues


def refine_phone(raw: Any) -> PhoneResult:
    text = _phone_digits_text(raw)
    if not text:
        return PhoneResult('', None)
    if any(ord(ch) < 32 and ch not in '\n\r\t' for ch in text):
        text = ''.join(ch for ch in text if ord(ch) >= 32 or ch in '\n\r\t')
    parts = [p for p in re.split(r'\s*(?:/|;|,|\||\bor\b|\n|&)\s*', text, flags=re.I) if p and re.search(r'\d', p)]
    parts = [p for p in parts if len(re.sub(r'\D', '', p)) >= 7] or parts
    if len(parts) <= 1:
        display, e164, ext, tf, issues = _parse_one(text)
        return PhoneResult(display, e164, ext, [], tf, issues)
    display, e164, ext, tf, issues = _parse_one(parts[0])
    alternates = []
    for p in parts[1:]:
        d2, e2, _x, _t, _i = _parse_one(p)
        alternates.append(d2 if e2 else p.strip())
    issues.append(Issue('phone', 'amber', 'multiple', f'{len(parts)} numbers found; the first is primary and the rest are kept as alternates.'))
    return PhoneResult(display, e164, ext, alternates, tf, issues)


# ---------------------------------------------------------------- emails
_EMAIL = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,24}$")
_DOMAIN_TYPOS = {
    'gmial.com': 'gmail.com', 'gmal.com': 'gmail.com', 'gmai.com': 'gmail.com', 'gamil.com': 'gmail.com',
    'gmail.co': 'gmail.com', 'gmail.con': 'gmail.com', 'gmail.cm': 'gmail.com', 'gmaill.com': 'gmail.com', 'gnail.com': 'gmail.com',
    'yaho.com': 'yahoo.com', 'yahooo.com': 'yahoo.com', 'yahoo.co': 'yahoo.com', 'yahoo.con': 'yahoo.com', 'yhoo.com': 'yahoo.com',
    'hotmial.com': 'hotmail.com', 'hotmal.com': 'hotmail.com', 'hotmail.co': 'hotmail.com', 'hotmail.con': 'hotmail.com', 'hotmai.com': 'hotmail.com',
    'outlok.com': 'outlook.com', 'outloo.com': 'outlook.com', 'outlook.co': 'outlook.com', 'outlook.con': 'outlook.com',
    'aol.con': 'aol.com', 'icloud.con': 'icloud.com', 'iclod.com': 'icloud.com',
}
_TLD_TYPOS = {'con': 'com', 'cmo': 'com', 'ocm': 'com', 'comm': 'com', 'vom': 'com', 'xom': 'com', 'cpm': 'com'}


def _clean_email(s: str) -> str:
    s = s.strip()
    m = re.search(r'<([^<>]*)>', s)
    if m:
        s = m.group(1)
    s = re.sub(r'^\s*mailto:', '', s, flags=re.I)
    s = re.sub(r'\s+', '', s)
    s = s.strip('<>"\'')
    s = re.sub(r'[.,;:]+$', '', s)
    return s.lower()


def _suggest(addr: str) -> str | None:
    s = addr
    s = re.sub(r'@{2,}', '@', s)
    if s.count('@') == 1:
        local, dom = s.split('@')
        dom = dom.replace(',', '.')
        dom = re.sub(r'\.{2,}', '.', dom)
        if dom in _DOMAIN_TYPOS:
            dom = _DOMAIN_TYPOS[dom]
        else:
            parts = dom.rsplit('.', 1)
            if len(parts) == 2 and parts[1] in _TLD_TYPOS:
                dom = parts[0] + '.' + _TLD_TYPOS[parts[1]]
                dom = _DOMAIN_TYPOS.get(dom, dom)
        s = local + '@' + dom
    return s if s != addr else None


def refine_email(raw: Any) -> EmailResult:
    text = '' if raw is None else str(raw)
    text = text.strip()
    if not text:
        return EmailResult('')
    pieces = [p for p in re.split(r'[;,/\s]+(?=[^;,/\s]*@)', text) if p.strip()] if text.count('@') > 1 and not re.search(r'@\s*@', text) else [text]
    if len(pieces) > 1 and all('@' in p for p in pieces):
        cleaned = [_clean_email(p) for p in pieces]
        cleaned = [c for c in cleaned if c]
        primary = refine_email(cleaned[0])
        primary.alternates = cleaned[1:]
        primary.issues.append(Issue('email', 'amber', 'multiple', f'{len(cleaned)} addresses found; the first is primary and the rest are kept as alternates.'))
        return primary
    value = _clean_email(text)
    issues: list[Issue] = []
    suggestion = _suggest(value)
    valid = bool(_EMAIL.fullmatch(value)) and '..' not in value
    if suggestion:
        issues.append(Issue('email', 'amber', 'typo', f'Did you mean {suggestion}?'))
    if not valid:
        if suggestion and _EMAIL.fullmatch(suggestion):
            issues.append(Issue('email', 'red', 'invalid', f'Email is not valid as typed. Accept the suggestion {suggestion} to fix it.'))
        else:
            issues.append(Issue('email', 'red', 'invalid', 'Email address is not valid.'))
    return EmailResult(value, [], suggestion, issues)


# ---------------------------------------------------------------- dates
_MONTHS = {m: i + 1 for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}
_MONTHS['sept'] = 9


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(raw: Any, today: str, field_name: str = 'business_date', default_today: bool = True) -> DateResult:
    """Parse a pasted date. `today` is the Pacific business date (YYYY-MM-DD)."""
    t = date.fromisoformat(today)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if default_today:
            return DateResult(today, [Issue(field_name, 'green', 'defaulted', 'Empty date set to today (Pacific).')])
        return DateResult(None)
    if isinstance(raw, bool):
        return DateResult(None, [Issue(field_name, 'red', 'invalid', 'Date could not be read.')])
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and re.fullmatch(r'\d{5}(?:\.0+)?', raw.strip())):
        n = float(raw)
        if not n.is_integer() or not (20000 <= n <= 80000):
            return DateResult(None, [Issue(field_name, 'red', 'invalid', 'Date number is not a valid Excel date.')])
        return DateResult((date(1899, 12, 30) + timedelta(days=int(n))).isoformat())
    s = str(raw).strip()
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:\s?[ap]m)?(?:Z|[+-]\d{2}:?\d{2})?$', '', s, flags=re.I).strip()
    m = re.fullmatch(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        d = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return DateResult(d.isoformat()) if d else DateResult(None, [Issue(field_name, 'red', 'invalid', 'Date does not exist.')])
    m = re.fullmatch(r'(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})', s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        if a > 12 and b <= 12:
            d = _mk(y, b, a)
            if d:
                return DateResult(d.isoformat(), [Issue(field_name, 'amber', 'ambiguous', f'Interpreted as {d.strftime("%b")} {d.day}, {d.year} (day/month order).')])
        d = _mk(y, a, b)
        return DateResult(d.isoformat()) if d else DateResult(None, [Issue(field_name, 'red', 'invalid', 'Date does not exist.')])
    m = re.fullmatch(r'(\d{1,2})[\s\-]([A-Za-z]{3,9})\.?[\s\-,]*(\d{2}|\d{4})?', s)
    if (m and m.group(2).lower()[:4].rstrip('t') in _MONTHS) or (m and m.group(2).lower()[:3] in _MONTHS):
        mon = _MONTHS.get(m.group(2).lower()[:3])
        y = int(m.group(3)) if m.group(3) else t.year
        if y < 100:
            y += 2000
        d = _mk(y, mon or 0, int(m.group(1)))
        if d:
            return DateResult(d.isoformat(), [] if m.group(3) else [Issue(field_name, 'green', 'year_assumed', f'Year {y} assumed.')])
    m = re.fullmatch(r'([A-Za-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)?,? ?(\d{4})?', s)
    if m and m.group(1).lower()[:3] in _MONTHS:
        y = int(m.group(3)) if m.group(3) else t.year
        d = _mk(y, _MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
        if d:
            return DateResult(d.isoformat(), [] if m.group(3) else [Issue(field_name, 'green', 'year_assumed', f'Year {y} assumed.')])
    return DateResult(None, [Issue(field_name, 'red', 'invalid', 'Use MM/DD/YYYY, YYYY-MM-DD, an Excel date or a month name.')])


# ---------------------------------------------------------------- toggles
_TRUE = {'yes', 'y', 'true', 't', '1', 'x', '✓', '✔', 'done'}
_FALSE = {'no', 'n', 'false', 'f', '0'}
_UNSET = {'', 'unset', 'n/a', 'na', '-', '—'}


def parse_tri(raw: Any) -> bool | None:
    """Yes/No/Unset. Raises ValueError for anything else."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and raw in (0, 1):
        return bool(raw)
    s = str(raw).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    if s in _UNSET:
        return None
    raise ValueError(f'"{raw}" is not Yes, No or blank.')


# ---------------------------------------------------------------- rows
ROW_TOGGLES = ('called', 'connected', 'text_sent', 'email_sent', 'followup_needed', 'strong_followup', 'sign_up', 'call_again')


def _text(raw: Any, limit: int) -> str:
    s = '' if raw is None else str(raw)
    s = ''.join(ch for ch in s if ord(ch) >= 32 or ch in '\n\t')
    return s.strip()[:limit]


def refine_row(row: dict[str, Any], today: str, comma_suffix: bool = True) -> dict[str, Any]:
    """Refine one pasted/typed row. Returns {'refined': {...}, 'issues': [...], 'severity': str}."""
    issues: list[Issue] = []
    name = refine_name(row.get('name', row.get('provider_name')), comma_suffix)
    issues += name.issues
    phone = refine_phone(row.get('phone'))
    issues += phone.issues
    email = refine_email(row.get('email'))
    issues += email.issues
    bdate = parse_date(row.get('business_date', row.get('date')), today)
    issues += bdate.issues
    nf = parse_date(row.get('next_followup'), today, 'next_followup', default_today=False)
    issues += nf.issues
    lf = parse_date(row.get('last_followup'), today, 'last_followup', default_today=False)
    issues += lf.issues
    toggles: dict[str, bool | None] = {}
    for k in ROW_TOGGLES:
        try:
            toggles[k] = parse_tri(row.get(k))
        except ValueError as e:
            toggles[k] = None
            issues.append(Issue(k, 'red', 'invalid', str(e)))
    if toggles['connected'] is True and toggles['called'] is not True:
        issues.append(Issue('connected', 'red', 'contradiction', 'Connected requires Called = Yes.'))
    if toggles['called'] is False and any(toggles[k] is True for k in ('connected',)):
        pass
    count_raw = row.get('followup_count')
    followup_count = None
    if count_raw not in (None, ''):
        try:
            followup_count = int(float(str(count_raw).strip()))
            if followup_count < 0:
                raise ValueError
        except ValueError:
            issues.append(Issue('followup_count', 'amber', 'invalid', '# Follow-Ups is not a number; it is derived from activity history.'))
    refined = {
        'name': name.value, 'kind': name.kind, 'credentials': name.credentials, 'entity_suffix': name.entity_suffix,
        'phone': phone.display, 'phone_e164': phone.e164, 'extension': phone.extension, 'phone_alternates': phone.alternates,
        'toll_free': phone.toll_free,
        'email': email.value, 'email_alternates': email.alternates, 'email_suggestion': email.suggestion,
        'business_date': bdate.value, 'next_followup': nf.value, 'last_followup': lf.value,
        **toggles,
        'followup_count': followup_count,
        'remarks': _text(row.get('remarks'), 6000),
    }
    order = {'green': 0, 'amber': 1, 'red': 2}
    severity = max((i.severity for i in issues), key=lambda s: order[s], default='green')
    return {'refined': refined, 'issues': [asdict(i) for i in issues], 'severity': severity}

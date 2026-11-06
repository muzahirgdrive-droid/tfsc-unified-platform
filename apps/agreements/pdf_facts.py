"""What a signed agreement PDF says about itself: how it was signed, with which company, which agreement, when and by whom.

Used when a past agreement is added to a client and when previous clients are imported. Only the PDF's text layer is read
(pypdf); nothing in the file is run or rendered. A scanned paper copy has no text, so nothing is found and the person types
the details. Every value found is a suggestion shown as "from file": the person checks it before saving.
"""
from __future__ import annotations

import datetime as dt
import io
import re

MAX_PAGES = 60
MAX_TEXT = 400_000
EXCERPT = (14_000, 6_000)       # normalized text kept for matching client names (start, end)

KIND_TITLES = {'baa': 'Business Associate Agreement', 'services': 'Services Agreement', 'nda': 'Mutual Nondisclosure Agreement', 'other': 'Agreement'}
KIND_PATTERNS = (
    ('baa', re.compile(r'business\s+associate\s+(?:agreement|addendum|contract)|\bBAA\b', re.I)),
    ('services', re.compile(r'\b(?:master\s+)?services?\s+agreement\b', re.I)),
    ('nda', re.compile(r'\bnon-?\s?disclosure\s+agreement|\bmutual\s+nda\b|\bNDA\b|\bconfidentiality\s+agreement', re.I)),
)
DOCUSIGN = re.compile(r'docu\s?sign\s+envelope\s+id\s*:?\s*([0-9A-F]{8}-?[0-9A-F]{4}-?[0-9A-F]{4}-?[0-9A-F]{4}-?[0-9A-F]{12})', re.I)
JOTFORM_ID = re.compile(r'submission\s*(?:id|#|no\.?|number)?\s*[:#]?\s*(\d{6,25})', re.I)
MONTHS = {m: i for i, names in enumerate((('jan', 'january'), ('feb', 'february'), ('mar', 'march'), ('apr', 'april'), ('may',), ('jun', 'june'),
                                          ('jul', 'july'), ('aug', 'august'), ('sep', 'sept', 'september'), ('oct', 'october'), ('nov', 'november'),
                                          ('dec', 'december')), start=1) for m in names}
_MONTH = r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
DATE = re.compile(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b'                       # 3/4/2022, 03-04-2022 (month first, US)
                  r'|\b(\d{4})-(\d{2})-(\d{2})\b'                                 # 2022-03-04
                  rf'|\b{_MONTH}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b'   # March 4, 2022
                  rf'|\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?{_MONTH},?\s+(\d{{4}})\b', re.I)  # 4 March 2022, 4th day of March, 2022
NAME_WORD = r"[A-Z][A-Za-z.'\-]+"
STOP_WORDS = {'title', 'date', 'signature', 'signed', 'by', 'email', 'name', 'its', 'company', 'client', 'printed', 'print', 'address', 'phone',
              'docusigned', 'covered', 'entity', 'business', 'associate', 'llc', 'inc', 'pllc'}


def _date(m: re.Match) -> dt.date | None:
    g = m.groups()
    try:
        if g[0]:
            value = dt.date(int(g[2]), int(g[0]), int(g[1]))
        elif g[3]:
            value = dt.date(int(g[3]), int(g[4]), int(g[5]))
        elif g[6]:
            value = dt.date(int(g[8]), MONTHS[g[6].lower()[:3]], int(g[7]))
        else:
            value = dt.date(int(g[11]), MONTHS[g[10].lower()[:3]], int(g[9]))
    except (ValueError, KeyError):
        return None
    return value if 1990 <= value.year <= 2100 else None


def dates_after(text: str, pattern: str, window: int = 60, exclude_before: str = '') -> list[dt.date]:
    """Dates that follow the pattern within `window` characters."""
    out = []
    for m in re.finditer(pattern, text, re.I):
        if exclude_before and re.search(exclude_before, text[max(0, m.start() - 25):m.start()], re.I):
            continue
        d = DATE.search(text, m.end(), m.end() + window)
        if d and (value := _date(d)):
            out.append(value)
    return out


def norm(value: str) -> str:
    """Lower case letters and digits separated by single spaces: how names are compared."""
    return re.sub(r'[^a-z0-9]+', ' ', (value or '').lower()).strip()


def extract_text(raw: bytes) -> tuple[str, int]:
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            reader.decrypt('')
        pages = len(reader.pages)
    except Exception:
        return '', 0
    parts, size = [], 0
    for page in reader.pages[:MAX_PAGES]:
        try:
            part = page.extract_text() or ''
        except Exception:
            part = ''
        parts.append(part)
        size += len(part)
        if size > MAX_TEXT:
            break
    return '\n'.join(parts)[:MAX_TEXT], pages


def _company(text: str, companies) -> str:
    """The company named most often (legal, display or short name)."""
    haystack = ' ' + norm(text) + ' '
    best, hits_best = '', 0
    for e in companies:
        names = {norm(n) for n in (e.get('name'), e.get('display'), e.get('short')) if n and len(norm(n)) >= 4}
        hits = sum(haystack.count(' ' + n + ' ') for n in names)
        if hits > hits_best:
            best, hits_best = e['name'], hits
    return best


def _kind(text: str) -> str:
    head = text[:4000]
    for part in (head, text):
        found = [(m.start(), kind) for kind, pattern in KIND_PATTERNS if (m := pattern.search(part))]
        if found:
            return min(found)[1]
    return ''


def _person(raw: str) -> str:
    words = []
    for w in raw.split():
        if w.lower().strip('.:,') in STOP_WORDS:
            break
        words.append(w.strip(','))
    return ' '.join(words) if 2 <= len(words) <= 4 else ''


def _signer(text: str, company_people: set[str]) -> tuple[str, str]:
    """The client's signer when the document names one person who is not one of our companies' signatories."""
    found: list[tuple[str, str]] = []
    for m in re.finditer(rf'(?:Printed\s+Name|Print\s+Name|Signer\s+Name|Name)\s*:\s*({NAME_WORD}(?:[ \t]+{NAME_WORD}){{1,5}})', text):
        person = _person(m.group(1))
        if not person or person.lower() in company_people:
            continue
        title = re.search(r'Title\s*:\s*([A-Za-z][A-Za-z ,.&/\-]{1,60}?)\s*(?:\n|$|Date\b|Signature\b|Email\b|Name\b)', text[m.end():m.end() + 160])
        found.append((person, title.group(1).strip(' ,') if title else ''))
    for m in re.finditer(rf'Signer\s+Events\s+Signature\s+Timestamp\s+({NAME_WORD}(?:[ \t]+{NAME_WORD}){{1,3}})', text):
        person = _person(m.group(1))
        if person and person.lower() not in company_people:
            found.append((person, ''))
    people = list(dict.fromkeys(p for p, _ in found))
    if len(people) != 1:
        return '', ''
    title = next((t for p, t in found if t), '')
    return people[0], title


def kind_from_name(file_name: str) -> str:
    """The agreement a file name points to ("Desert Rose BAA.pdf"), for files with no text."""
    words = set(norm(re.sub(r'\.pdf$', '', file_name or '', flags=re.I)).split())
    if 'baa' in words or {'business', 'associate'} <= words:
        return 'baa'
    if words & {'nda', 'nondisclosure'}:
        return 'nda'
    if words & {'sa', 'msa', 'services', 'service'}:
        return 'services'
    return ''


def read(raw: bytes, companies, company_people: set[str] | None = None, file_name: str = '') -> dict:
    """What the file says. companies: dicts with name/display/short (every company, built-in and added)."""
    text, pages = extract_text(raw)
    out = {'pages': pages, 'text': bool(text.strip()), 'source': '', 'reference': '', 'entity': '', 'kind': '', 'signed_on': '', 'effective_on': '',
           'client_signer': '', 'client_title': '', 'excerpt': ''}
    lower_name = (file_name or '').lower()
    if not text.strip():
        out['kind'] = kind_from_name(file_name)
        if 'jotform' in lower_name:
            out['source'] = 'jotform'
            m = re.search(r'jotform[_\s-]*(\d{6,25})', lower_name)
            out['reference'] = m.group(1) if m else ''
        elif 'docusign' in lower_name:
            out['source'] = 'docusign'
        return out
    flat = re.sub(r'[ \t ]+', ' ', text)
    m = DOCUSIGN.search(flat)
    if m:
        out['source'] = 'docusign'
        ref = m.group(1).upper().replace('-', '')
        out['reference'] = '-'.join((ref[:8], ref[8:12], ref[12:16], ref[16:20], ref[20:]))
    elif 'jotform' in flat.lower() or 'jotform' in lower_name:
        out['source'] = 'jotform'
        m = JOTFORM_ID.search(flat) or re.search(r'jotform[_\s-]*(\d{6,25})', lower_name)
        out['reference'] = m.group(1) if m else ''
    out['entity'] = _company(flat, companies)
    out['kind'] = _kind(flat) or kind_from_name(file_name)
    if out['source'] == 'docusign':
        signed = dates_after(flat, r'\bCompleted\b(?:\s+Security\s+Checked)?', 50) or dates_after(flat, r'\bSigned\s*:', 40)
    else:
        signed = dates_after(flat, r'\bSigned\s*(?:on|date)?\s*:|\bDate\s+Signed\s*:?|\bDate\s*:', 40, exclude_before=r'effective') \
            or dates_after(flat, r'\bSignature\b', 160)
    if signed:
        out['signed_on'] = max(signed).isoformat()
    effective = dates_after(flat, r'\beffective\s+(?:date\s*(?:is|of)?\s*:?|as\s+of)', 40)
    if effective:
        out['effective_on'] = effective[0].isoformat()
    out['client_signer'], out['client_title'] = _signer(flat, {p.lower() for p in (company_people or set()) if p})
    normalized = norm(flat)
    start, end = EXCERPT
    out['excerpt'] = normalized if len(normalized) <= start + end else normalized[:start] + ' ' + normalized[-end:]
    return out


def public(found: dict) -> dict:
    """What the browser is shown (the text excerpt stays on the server)."""
    return {k: v for k, v in (found or {}).items() if k != 'excerpt'}

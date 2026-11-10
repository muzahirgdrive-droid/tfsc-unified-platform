"""Import previous clients: clients and signed agreements from before this portal (the manual email method, DocuSign, Jotform).

Three steps. 1. Upload an optional spreadsheet (the template's columns, or similar headings) and any number of signed PDFs, one
request per file (each checked for viruses, read for what it says: pdf_facts). 2. Check the matches: each file is matched to a
client already in the portal (by client number, the DocuSign envelope, the client's name in the file or the file name), to a
new client, or left for the person to choose. 3. Import: one transaction creates the new clients (numbered oldest signature
first), the past agreements and their signed copies.

Nothing reaches the client records before step 3: uploads wait in a staging area (client_imports, client_import_files) that
is removed after the import or when it is cancelled, and forgotten after seven days. Only Admins and the Super Admin import;
they match against, and create, clients within their own scope.
"""
from __future__ import annotations

import base64
import csv
import datetime as dt
import hashlib
import io
import re
import time

import client_numbers
import client_records as records
import clients as client_profiles
import database as db
import esign
import pdf_facts
import security as sec
from entities import ENTITIES
from esign_db import client_agreements, client_files, client_import_files, client_imports
from fastapi import Body, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import case, delete, func, insert, literal, select, update

KEEP_DAYS = 7
MAX_FILES = 500
MAX_ROWS = 5000
FILE_LIMIT = 15_000_000
SHEET_LIMIT = 5_000_000
FIELDS = (('name', 'Client name'), ('address', 'Address'), ('npi', 'NPI'), ('email', 'Email'), ('company', 'Company'), ('agreement', 'Agreement'),
          ('signed_on', 'Signed on'), ('source', 'Signed through'), ('effective_on', 'Effective date'), ('client_signer', 'Client signer'), ('notes', 'Notes'))
OPTIONAL = (('number', 'Client no.'), ('file', 'File name'))
LABELS = dict(FIELDS + OPTIONAL)
# Headings people use for each column (compared in lower case, letters and digits only).
SYNONYMS = {
    'name': ('client name', 'client', 'practice name', 'practice', 'client practice', 'name', 'client legal name', 'legal name', 'organization', 'company name client'),
    'address': ('address', 'client address', 'street address', 'practice address', 'mailing address'),
    'npi': ('npi', 'npi number', 'group npi', 'organization npi'),
    'email': ('email', 'e mail', 'client email', 'email address', 'contact email'),
    'company': ('company', 'contracting company', 'our company', 'entity', 'source company', 'signed with'),
    'agreement': ('agreement', 'agreement type', 'document', 'document type', 'type', 'contract', 'contract type'),
    'signed_on': ('signed on', 'signed date', 'date signed', 'signed', 'signature date', 'executed', 'executed on', 'completed', 'completed on', 'date'),
    'source': ('signed through', 'method', 'signed via', 'platform', 'how signed', 'signing method', 'system'),
    'effective_on': ('effective date', 'effective', 'effective on', 'start date'),
    'client_signer': ('client signer', 'signer', 'signed by', 'signatory', 'client signatory'),
    'notes': ('notes', 'note', 'comments', 'comment', 'remarks'),
    'number': ('client no', 'client number', 'client no.', 'client id'),
    'file': ('file name', 'file', 'pdf', 'filename', 'document file'),
}
SUFFIXES = {'llc', 'pllc', 'inc', 'incorporated', 'pc', 'pa', 'ltd', 'corp', 'corporation', 'co', 'lp', 'llp', 'plc', 'dba'}
FILE_WORDS = {'baa', 'business', 'associate', 'agreement', 'agreements', 'signed', 'executed', 'final', 'copy', 'services', 'service', 'sa', 'nda', 'mutual',
              'docusign', 'jotform', 'scan', 'scanned', 'completed', 'contract', 'fully', 'countersigned', 'pdf', 'and', 'nondisclosure', 'non', 'disclosure',
              'addendum', 'form', 'submission', 'version', 'v1', 'v2', 'v3', 'draft', 'the'}


# ------------------------------------------------------------------ values
def name_key(value: str) -> str:
    words = pdf_facts.norm(value).split()
    while words and words[0] == 'the':
        words = words[1:]
    while len(words) > 1 and words[-1] in SUFFIXES:
        words = words[:-1]
    return ' '.join(words)


def name_from_file(file_name: str) -> str:
    stem = re.sub(r'\.[A-Za-z0-9]{2,4}$', '', file_name or '')
    words = [w for w in re.split(r'[\s_]+', re.sub(r'[()\[\]{}]', ' ', stem)) if w]
    kept = []
    for w in words:
        bare = re.sub(r'[^A-Za-z0-9&]', '', w)
        if not bare or bare.lower() in FILE_WORDS or re.fullmatch(r'\d{3,}|cl\d+', bare.lower()):
            continue
        kept.append(w.strip('-,.;:'))
    return re.sub(r'\s+', ' ', ' '.join(k for k in kept if k)).strip(' -,')[:200]


def kind_from(text: str) -> str:
    n = ' ' + pdf_facts.norm(text) + ' '
    if not n.strip():
        return ''
    if ' baa ' in n or ' business associate ' in n:
        return 'baa'
    if ' nda ' in n or ' nondisclosure ' in n or ' non disclosure ' in n or ' confidentiality ' in n:
        return 'nda'
    if ' service ' in n or ' services ' in n or ' sa ' in n or ' msa ' in n:
        return 'services'
    return 'other'


def source_from(text: str) -> str:
    n = pdf_facts.norm(text)
    if not n:
        return ''
    if 'docusign' in n.replace(' ', ''):
        return 'docusign'
    if 'jotform' in n.replace(' ', ''):
        return 'jotform'
    if any(w in n for w in ('manual', 'email', 'e mail', 'mail', 'paper', 'scan', 'fax', 'wet')):
        return 'manual'
    return 'other'


def company_from(text: str) -> str:
    key = pdf_facts.norm(text)
    if not key:
        return ''
    for e in ENTITIES.values():
        names = {pdf_facts.norm(x) for x in (e.get('name'), e.get('display'), e.get('short'), e.get('key')) if x}
        if key in names or name_key(text) in {name_key(x) for x in (e.get('name'), e.get('display'), e.get('short')) if x}:
            return e['name']
    # Otherwise only an unmistakable match counts: the company's short name appears in the text, or the text is the start of
    # exactly one company's name. "Billing" alone fits two companies, so it is left for the person to choose.
    found = [e['name'] for e in ENTITIES.values()
             if (short := pdf_facts.norm(e.get('short') or '')) and len(key) >= 3 and (short in key or short.startswith(key))]
    return found[0] if len(found) == 1 else ''


def date_from(value) -> str:
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value or '').strip()
    if not text:
        return ''
    m = pdf_facts.DATE.search(text)
    d = pdf_facts._date(m) if m else None
    if not d:
        m = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{2})', text)
        if m:
            try:
                d = dt.date(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2)))
            except ValueError:
                d = None
    return d.isoformat() if d else ''


# ------------------------------------------------------------------ the spreadsheet
def field_of(header: str) -> str | None:
    key = pdf_facts.norm(header)
    for field, names in SYNONYMS.items():
        if key in {pdf_facts.norm(n) for n in names}:
            return field
    return None


def parse_sheet(name: str, raw: bytes) -> dict:
    lower = name.lower()
    if lower.endswith('.xlsx'):
        if not raw.startswith(b'PK\x03\x04'):
            raise ValueError('The spreadsheet could not be read. Save it as .xlsx or .csv and upload it again.')
        from openpyxl import load_workbook
        try:
            book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            sheet = book.worksheets[0]
            table = [list(r) for _, r in zip(range(MAX_ROWS + 50), sheet.iter_rows(values_only=True), strict=False)]
            book.close()
        except Exception:
            raise ValueError('The spreadsheet could not be read. Save it as .xlsx or .csv and upload it again.') from None
    elif lower.endswith('.csv'):
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('cp1252', errors='replace')
        table = list(csv.reader(io.StringIO(text)))[:MAX_ROWS + 50]
    else:
        raise ValueError('Upload the spreadsheet as .xlsx or .csv.')
    table = [r for r in table if any(str(v or '').strip() for v in r)]
    if not table:
        raise ValueError('The spreadsheet is empty.')
    headers = [str(v or '').strip()[:80] for v in table[0]]
    mapping, used = [], set()
    for h in headers:
        f = field_of(h)
        if f in used:
            f = None
        used.add(f)
        mapping.append([h, f])
    if 'name' not in used:
        raise ValueError('The spreadsheet needs a "Client name" column. Download the template to see the columns.')
    rows = []
    for n, r in enumerate(table[1:MAX_ROWS + 1], start=2):
        row = {'row': n}
        for (_, f), v in zip(mapping, r, strict=False):
            if f:
                row[f] = v.isoformat()[:10] if isinstance(v, (dt.date, dt.datetime)) else str(v if v is not None else '').strip()[:4000]
        if row.get('name'):
            rows.append(row)
    if len(table) - 1 > MAX_ROWS:
        raise ValueError(f'Import up to {MAX_ROWS} rows at a time.')
    return {'name': name, 'headers': headers, 'mapping': mapping, 'rows': rows, 'columns': len(headers)}


def template(fmt: str) -> tuple[bytes, str, str]:
    head = [label for _, label in FIELDS]
    if fmt == 'csv':
        out = io.StringIO()
        csv.writer(out).writerow(head)
        return ('﻿' + out.getvalue()).encode('utf-8'), 'text/csv; charset=utf-8', 'Previous clients template.csv'
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = 'Clients'
    ws.append(head)
    for i, cell in enumerate(ws[1], start=1):
        cell.fill, cell.font = PatternFill('solid', fgColor='0B2545'), Font(bold=True, color='FFFFFF')
        ws.column_dimensions[get_column_letter(i)].width = 22
    ws.freeze_panes = 'A2'
    help_sheet = wb.create_sheet('How to fill it in')
    for line in (
            ('Column', 'What to enter'),
            ('Client name', "The client's legal name. Required. A client already in the portal with the same name keeps its number."),
            ('Address', 'Street, city, state and ZIP, e.g. 1200 Barton Springs Rd, Austin, TX 78704.'),
            ('NPI', 'The 10-digit NPI, when known.'), ('Email', "The client's email address, when known."),
            ('Company', 'Our company the agreement was signed with, e.g. 24-7 Consultancy, Scribe Align, Extended Health Services.'),
            ('Agreement', 'BAA, Services Agreement, NDA or Other.'), ('Signed on', 'The date the agreement was signed, e.g. 2022-03-04 or 3/4/2022.'),
            ('Signed through', 'DocuSign, Manual (email), Jotform or Other.'), ('Effective date', 'The effective date written on the agreement, if different.'),
            ('Client signer', 'Who signed for the client, e.g. Dana Whitfield, Owner.'), ('Notes', 'Anything to keep with the agreement.'),
            ('', ''), ('Signed copies', 'Upload the signed PDFs with the spreadsheet: each is matched to its client by name. One row per agreement.')):
        help_sheet.append(list(line))
    help_sheet.column_dimensions['A'].width, help_sheet.column_dimensions['B'].width = 18, 110
    for cell in help_sheet[1]:
        cell.font = Font(bold=True)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'Previous clients template.xlsx'


# ------------------------------------------------------------------ the session
def purge(c) -> None:
    old = select(client_imports.c.id).where(client_imports.c.expires < time.time())
    c.execute(delete(client_import_files).where(client_import_files.c.import_id.in_(old)))
    c.execute(delete(client_imports).where(client_imports.c.expires < time.time()))


def admin_only(actor: dict) -> None:
    if actor['role'] == 'user':
        raise HTTPException(403, 'Only an Admin or the Super Admin can import clients.')


def session_for(c, actor: dict, import_id: str) -> dict:
    admin_only(actor)
    row = c.execute(select(client_imports).where(client_imports.c.id == import_id, client_imports.c.owner_id == actor['id'],
                                                 client_imports.c.expires >= time.time())).mappings().first()
    if not row:
        raise HTTPException(404, 'This import was not found. It may have been finished, cancelled or left for more than seven days.')
    out = dict(row)
    out['choices'] = dict(out['choices'] or {})
    out['choices'].setdefault('items', {})
    out['choices'].setdefault('defaults', {})
    return out


def touch(c, session_id: str, **values) -> None:
    t = time.time()
    c.execute(update(client_imports).where(client_imports.c.id == session_id).values(updated=t, expires=t + KEEP_DAYS * 86400, **values))


def defaults_of(actor: dict, session: dict) -> dict:
    d = dict(session['choices'].get('defaults') or {})
    allowed = records.visible_companies(actor)
    if d.get('entity') not in allowed:
        d['entity'] = allowed[0] if len(allowed) == 1 else ''
    if d.get('kind') not in records.KINDS:
        d['kind'] = 'baa'
    if d.get('source') not in records.PAST_SOURCES:
        d['source'] = 'manual'
    return d


# ------------------------------------------------------------------ matching
def client_index(c, actor: dict) -> dict:
    rows = [dict(r) for r in c.execute(select(db.clients.c.id, db.clients.c.client_number, db.clients.c.name, db.clients.c.address, db.clients.c.profile,
                                              db.clients.c.test, db.clients.c.draft['entity'].as_string().label('draft_entity'))
                                       .where(sec.scope(actor, db.clients.c.owner_id), db.clients.c.deleted_at.is_(None))).mappings()]
    by_key: dict[str, list] = {}
    for r in rows:
        r['key'] = name_key(r['name'])
        r['words'] = set(r['key'].split())
        by_key.setdefault(r['key'], []).append(r)
    refs = dict(c.execute(select(client_agreements.c.reference, client_agreements.c.client_id)
                          .where(client_agreements.c.reference != '', client_agreements.c.client_id.in_([r['id'] for r in rows] or ['']))).all()) if rows else {}
    known = {}
    if rows:
        for sha, cid, number in c.execute(select(client_files.c.sha256, client_files.c.client_id, db.clients.c.client_number)
                                          .join(client_agreements, client_agreements.c.file_id == client_files.c.id)
                                          .join(db.clients, db.clients.c.id == client_files.c.client_id)
                                          .where(sec.scope(actor, db.clients.c.owner_id))).all():
            known[sha] = (cid, number)
    return {'rows': rows, 'by_id': {r['id']: r for r in rows}, 'by_number': {r['client_number']: r for r in rows if r['client_number']},
            'by_key': by_key, 'refs': refs, 'known': known}


def brief(r: dict) -> dict:
    return {'id': r['id'], 'client_number': r['client_number'] or '', 'name': r['name']}


def in_text(key: str, words: set, excerpt: str, excerpt_words: set) -> bool:
    return len(key) >= 6 and words <= excerpt_words and (' ' + key + ' ') in excerpt


def compute(c, actor: dict, session: dict, files: list[dict]) -> dict:
    idx = client_index(c, actor)
    defaults = defaults_of(actor, session)
    choices = session['choices']['items']
    sheet = session.get('sheet') or {}
    rows = sheet.get('rows') or []
    # clients named by the spreadsheet: an existing client (by number or name) or a new one
    sheet_clients: dict[str, dict] = {}
    for r in rows:
        key = name_key(r['name'])
        if not key:
            continue
        sc = sheet_clients.setdefault(key, {'key': key, 'name': r['name'][:200], 'rows': [], 'existing': None, 'reason': ''})
        sc['rows'].append(r)
        if not sc['existing']:
            num = (client_numbers.search_terms(r.get('number', '')) or [''])[0]
            if num and num in idx['by_number']:
                sc['existing'], sc['reason'] = idx['by_number'][num], 'client number'
            elif len(idx['by_key'].get(key, [])) == 1:
                sc['existing'], sc['reason'] = idx['by_key'][key][0], 'name'
            elif len(idx['by_key'].get(key, [])) > 1:
                sc['ambiguous'] = idx['by_key'][key]
    by_file = {}
    for r in rows:
        if r.get('file'):
            by_file[pdf_facts.norm(re.sub(r'\.pdf$', '', r['file'], flags=re.I))] = r

    allowed = set(records.visible_companies(actor))

    def agreement_of(found: dict, row: dict | None, client: dict | None = None) -> dict:
        row = row or {}
        entity = company_from(row.get('company', '')) or found.get('entity') or ''
        # the file does not say: the company the client was set up with, when this person may use it
        from_client = not entity and bool(client) and client.get('draft_entity') in allowed
        entity = entity or (client.get('draft_entity') if from_client else '')
        kind = kind_from(row.get('agreement', '')) or found.get('kind') or ''
        source = source_from(row.get('source', '')) or found.get('source') or ''
        out = {'entity': entity, 'kind': kind, 'source': source,
               'signed_on': date_from(row.get('signed_on')) or found.get('signed_on') or '',
               'effective_on': date_from(row.get('effective_on')) or found.get('effective_on') or '',
               'client_signer': (row.get('client_signer') or ', '.join(x for x in (found.get('client_signer'), found.get('client_title')) if x))[:200],
               'reference': (found.get('reference') or '')[:120], 'notes': (row.get('notes') or '')[:4000],
               'from_file': sorted(k for k in ('entity', 'kind', 'source', 'signed_on', 'effective_on', 'client_signer') if found.get(k) and not row.get(
                   {'entity': 'company', 'kind': 'agreement'}.get(k, k)))}
        out['from_client'] = ['entity'] if from_client else []
        out['defaulted'] = [k for k in ('entity', 'kind', 'source') if not out[k]]
        for k in out['defaulted']:
            out[k] = defaults[k]
        return out

    items = []
    used_rows: set[int] = set()

    def target_sheet(sc: dict, reason: str) -> dict:
        if sc.get('existing'):
            return {'state': 'matched', 'reason': reason or sc['reason'], 'client': brief(sc['existing'])}
        if sc.get('ambiguous'):
            return {'state': 'choose', 'reason': 'same name', 'candidates': [brief(x) for x in sc['ambiguous']]}
        return {'state': 'new', 'reason': 'spreadsheet', 'new': sc['name'], 'new_key': sc['key']}

    files_by_id = {f['id']: f for f in files}
    for f in files:
        found = f['found'] or {}
        excerpt = ' ' + (found.get('excerpt') or '') + ' '
        words = set(excerpt.split())
        match, row = None, None
        stem = pdf_facts.norm(re.sub(r'\.pdf$', '', f['name'], flags=re.I))
        if stem in by_file:
            row = by_file[stem]
            match = target_sheet(sheet_clients[name_key(row['name'])], 'spreadsheet')
        if not match:
            m = re.search(r'\bcl[\s-]?(\d{4,7})\b', f['name'].lower()) or re.search(r'\bcl (\d{4,7})\b', excerpt)
            if m and client_numbers.format_number(int(m.group(1))) in idx['by_number']:
                match = {'state': 'matched', 'reason': 'client number', 'client': brief(idx['by_number'][client_numbers.format_number(int(m.group(1)))])}
        if not match and found.get('reference') and found['reference'] in idx['refs'] and idx['refs'][found['reference']] in idx['by_id']:
            match = {'state': 'matched', 'reason': 'envelope', 'client': brief(idx['by_id'][idx['refs'][found['reference']]])}
        if not match and found.get('excerpt'):
            hits = [r for r in idx['rows'] if in_text(r['key'], r['words'], excerpt, words)]
            sheet_hits = [sc for sc in sheet_clients.values() if in_text(sc['key'], set(sc['key'].split()), excerpt, words)]
            hits = [r for r in hits if not any(r['key'] != o['key'] and r['key'] in o['key'] for o in hits)]
            if len(hits) == 1:
                match = {'state': 'matched', 'reason': 'name in file', 'client': brief(hits[0])}
            elif len(hits) > 1:
                match = {'state': 'choose', 'reason': 'name in file', 'candidates': [brief(x) for x in hits[:8]]}
            elif sheet_hits:
                sheet_hits = [s for s in sheet_hits if not any(s['key'] != o['key'] and s['key'] in o['key'] for o in sheet_hits)]
                match = target_sheet(sheet_hits[0], 'name in file') if len(sheet_hits) == 1 else None
        items.append({'key': f['id'], 'type': 'file', 'file': {k: f[k] for k in ('id', 'name', 'size', 'scan', 'pages')}, 'text': bool(found.get('text')),
                      'match': match, 'row': row, 'found': found})

    # files of the same DocuSign envelope belong to the same client; then the file name; then a new client named after the file
    by_ref = {}
    for it in items:
        ref = it['found'].get('reference')
        if ref and it['match'] and it['match']['state'] == 'matched':
            by_ref.setdefault(ref, it['match'])
    for it in items:
        if it['match']:
            continue
        ref = it['found'].get('reference')
        if ref and ref in by_ref:
            it['match'] = dict(by_ref[ref], reason='envelope')
            continue
        guess = name_from_file(it['file']['name'])
        if guess:
            key = name_key(guess)
            exact = idx['by_key'].get(key, [])
            near = exact or [r for r in idx['rows'] if len(key) >= 5 and len(r['key']) >= 5 and (r['key'].startswith(key + ' ') or key.startswith(r['key'] + ' '))]
            if len(near) == 1:
                it['match'] = {'state': 'matched', 'reason': 'file name', 'client': brief(near[0])}
            elif len(near) > 1:
                it['match'] = {'state': 'choose', 'reason': 'file name', 'candidates': [brief(x) for x in near[:8]]}
            elif key in sheet_clients:
                it['match'] = target_sheet(sheet_clients[key], 'file name')
        if not it['match']:
            it['match'] = {'state': 'new', 'reason': 'file name', 'new': guess, 'new_key': name_key(guess)} if guess else \
                {'state': 'choose', 'reason': 'no name', 'candidates': []}
    for it in items:
        sha = files_by_id[it['key']]['sha256']
        if sha in idx['known']:
            number = idx['known'][sha][1]
            it['match'] = dict(it['match'], state='skip', reason='already on file', note=f'Already on file for {number or "a client"}')

    # a spreadsheet row for the same client and agreement adds its details to the file
    def client_key(match):
        return ('id', match['client']['id']) if match.get('client') else ('new', match.get('new_key'))
    rows_by_client: dict[tuple, list] = {}
    for sc in sheet_clients.values():
        target = target_sheet(sc, '')
        for r in sc['rows']:
            rows_by_client.setdefault(client_key(target), []).append(r)
    for it in items:
        if it['row']:
            used_rows.add(it['row']['row'])
    for it in items:
        if it['row'] or it['match']['state'] not in ('matched', 'new'):
            continue
        kind = it['found'].get('kind') or ''
        for r in rows_by_client.get(client_key(it['match']), []):
            if r['row'] in used_rows:
                continue
            rk = kind_from(r.get('agreement', ''))
            if (not rk or not kind or rk == kind) and (r.get('agreement') or r.get('signed_on')):
                it['row'] = r
                used_rows.add(r['row'])
                break
    # spreadsheet rows that no file uses: an agreement without a signed copy, or the client's details alone
    for sc in sheet_clients.values():
        for r in sc['rows']:
            if r['row'] in used_rows:
                continue
            has_agreement = bool(r.get('agreement') or r.get('signed_on'))
            items.append({'key': f"row-{r['row']}", 'type': 'row', 'row': r, 'match': target_sheet(sc, ''), 'found': {}, 'text': False,
                          'has_agreement': has_agreement})

    # the person's choices win
    for it in items:
        ch = choices.get(it['key'])
        if not ch:
            continue
        if ch.get('skip'):
            it['match'] = dict(it['match'], state='skip', reason='your choice')
        elif ch.get('client_id') and ch['client_id'] in idx['by_id']:
            it['match'] = {'state': 'matched', 'reason': 'your choice', 'client': brief(idx['by_id'][ch['client_id']])}
        elif ch.get('new'):
            it['match'] = {'state': 'new', 'reason': 'your choice', 'new': ch['new'][:200], 'new_key': name_key(ch['new'])}
        if it['match'].get('candidates') is None and ch:
            it['match']['candidates'] = []
    for it in items:
        if it['type'] == 'file' or it.get('has_agreement'):
            it['agreement'] = agreement_of(it['found'], it['row'], idx['by_id'].get((it['match'].get('client') or {}).get('id')))
        else:
            it['agreement'] = None
    # numbers the new clients will get: oldest signature first
    new = {}
    for it in items:
        if it['match']['state'] == 'new' and it['match'].get('new_key'):
            n = new.setdefault(it['match']['new_key'], {'key': it['match']['new_key'], 'name': it['match']['new'], 'earliest': ''})
            day = (it.get('agreement') or {}).get('signed_on') or ''
            if day and (not n['earliest'] or day < n['earliest']):
                n['earliest'] = day
    order = sorted(new.values(), key=lambda n: (not n['earliest'], n['earliest'], n['name'].casefold()))
    last, _ = client_numbers._last(c)
    for i, n in enumerate(order, start=1):
        n['number'] = client_numbers.format_number(last + i)
    for it in items:
        if it['match']['state'] == 'new' and it['match'].get('new_key') in new:
            it['match']['client_number'] = new[it['match']['new_key']]['number']
    counts = {'all': len(items), 'matched': sum(it['match']['state'] == 'matched' for it in items), 'new': sum(it['match']['state'] == 'new' for it in items),
              'choose': sum(it['match']['state'] == 'choose' for it in items), 'skip': sum(it['match']['state'] == 'skip' for it in items)}
    agreements = sum(1 for it in items if it['match']['state'] in ('matched', 'new') and it.get('agreement'))
    needs_company = sum(1 for it in items if it['match']['state'] in ('matched', 'new') and it.get('agreement') and 'entity' in it['agreement']['defaulted'])
    return {'items': items, 'counts': counts, 'new_clients': order, 'agreements': agreements, 'defaults': defaults, 'needs_company': needs_company,
            'sheet_clients': sheet_clients, 'index': idx}


def public_view(c, actor: dict, session: dict) -> dict:
    files = [dict(r) for r in c.execute(select(client_import_files.c.id, client_import_files.c.name, client_import_files.c.size, client_import_files.c.sha256,
                                               client_import_files.c.scan, client_import_files.c.pages, client_import_files.c.found)
                                        .where(client_import_files.c.import_id == session['id']).order_by(client_import_files.c.created, client_import_files.c.id)).mappings()]
    v = compute(c, actor, session, files)
    sheet = session.get('sheet') or None
    by_source = {}
    for it in v['items']:
        if it['type'] == 'file':
            s = (it.get('agreement') or {}).get('source') or ''
            by_source[s] = by_source.get(s, 0) + 1
    items = []
    for it in v['items']:
        a = it.get('agreement')
        items.append({'key': it['key'], 'type': it['type'], 'file': it.get('file'), 'row': it['row']['row'] if it.get('row') else None,
                      'row_name': it['row']['name'] if it.get('row') else '', 'text': it['text'], 'match': it['match'],
                      'agreement': a and dict(a, company=records.company_view(a['entity'])['short'] if a['entity'] else '')})
    return {'id': session['id'], 'created': session['created'], 'expires': session['expires'], 'items': items, 'counts': v['counts'],
            'new_clients': [{k: n[k] for k in ('name', 'number', 'earliest')} for n in v['new_clients']], 'agreements': v['agreements'],
            'defaults': v['defaults'], 'needs_company': v['needs_company'], 'files': len(files), 'by_source': by_source,
            'sheet': sheet and {'name': sheet['name'], 'rows': len(sheet['rows']), 'columns': sheet.get('columns', len(sheet['headers'])),
                                'mapping': [[h, LABELS.get(f) if f else ''] for h, f in sheet['mapping']]},
            'companies': [records.company_view(n) for n in records.visible_companies(actor)], 'kinds': records.KINDS,
            'sources': {k: records.SOURCES[k] for k in records.PAST_SOURCES}, 'limits': {'files': MAX_FILES, 'size': FILE_LIMIT}}


# ------------------------------------------------------------------ commit
def commit(c, actor: dict, session: dict) -> dict:
    files = [dict(r) for r in c.execute(select(client_import_files.c.id, client_import_files.c.name, client_import_files.c.size, client_import_files.c.sha256,
                                               client_import_files.c.scan, client_import_files.c.pages, client_import_files.c.found)
                                        .where(client_import_files.c.import_id == session['id'])).mappings()]
    # One import is committed once: a second click (or a second browser tab) waits here and then finds it gone.
    lock = select(client_imports.c.id).where(client_imports.c.id == session['id'])
    if c.dialect.name == 'postgresql':
        lock = lock.with_for_update()
    if not c.execute(lock).first():
        raise HTTPException(404, 'This import was already completed. Open Clients to see the result.')
    v = compute(c, actor, session, files)
    if v['counts']['choose']:
        raise HTTPException(400, f"Choose a client for the {v['counts']['choose']} item{'s' if v['counts']['choose'] != 1 else ''} marked "
                                 '"Choose a client", or skip them.')
    if v['needs_company'] and not v['defaults']['entity']:
        raise HTTPException(400, 'Some files do not say which company they were signed with. Choose the company to use for them.')
    live = [it for it in v['items'] if it['match']['state'] in ('matched', 'new')]
    if not live:
        raise HTTPException(400, 'There is nothing to import yet. Upload signed PDFs or a spreadsheet first.')
    today = dt.date.today().isoformat()
    for it in live:
        a = it.get('agreement')
        if a:
            sec.ensure_company(actor, a['entity'])
            if a.get('signed_on') and a['signed_on'] > today:
                raise HTTPException(400, f"A signed date in the future ({a['signed_on']}) cannot be right. Correct it in the spreadsheet or skip that row.")
    t = time.time()
    sheet_clients = v['sheet_clients']
    # new clients, oldest signature first, then numbered in that order
    created: dict[str, str] = {}
    for n in v['new_clients']:
        sc = sheet_clients.get(n['key']) or {}
        details = details_of(sc.get('rows') or [])
        existing = client_profiles.visible_client(c, actor, n['name'])
        if existing:
            created[n['key']] = existing[0]
            continue
        first = next((it['agreement']['entity'] for it in live if it['match'].get('new_key') == n['key'] and it.get('agreement')), '')
        entity = first or (actor['companies'][0] if actor['companies'] else next(iter(ENTITIES), ''))
        cid = db.uid()
        draft = {'client_name': n['name'], 'address': details.get('address', ''), 'entity': entity, 'client_email': details.get('email', ''), 'id': cid}
        profile = {k: details[k] for k in ('npi', 'email') if details.get(k)}
        c.execute(db.clients.insert().values(id=cid, owner_id=actor['id'], name=n['name'], name_key=client_profiles.name_key(n['name']),
                                             address=details.get('address', ''), draft=draft, updated=t, profile=profile, created=t, test=False))
        created[n['key']] = cid
    numbers = client_numbers.assign(c, list(dict.fromkeys(created.values())))
    for cid in created.values():
        db.log(c, actor, 'client.created', cid, {'source': 'import', 'client_number': numbers.get(cid, '')})
    # existing clients: the spreadsheet fills in details that are empty, never replaces them
    filled = 0
    for sc in sheet_clients.values():
        if not sc.get('existing'):
            continue
        filled += fill_details(c, actor, sc['existing']['id'], details_of(sc['rows']), t)
    clients_now = {r['id']: dict(r) for r in c.execute(select(db.clients.c.id, db.clients.c.client_number, db.clients.c.test, db.clients.c.name).where(
        db.clients.c.id.in_(list({it['match']['client']['id'] for it in live if it['match'].get('client')} | set(created.values())) or ['']))).mappings()}
    agreements = stored = already = 0
    sha_of = {f['id']: f['sha256'] for f in files}
    seen: set = set()
    for it in live:
        cid = it['match']['client']['id'] if it['match'].get('client') else created.get(it['match'].get('new_key'))
        if not cid or cid not in clients_now:
            continue
        a = it.get('agreement')
        file_id = None
        # The same past agreement is recorded once: importing a spreadsheet again, a repeated row or a file already on the
        # client's record adds nothing.
        if a:
            key = (cid, a['entity'], a['kind'], a['signed_on'] or '', a['reference'][:120], sha_of.get(it['file']['id'], '') if it['type'] == 'file' else '')
            on_file = c.execute(select(client_agreements.c.id).where(
                client_agreements.c.client_id == cid, client_agreements.c.entity == a['entity'], client_agreements.c.kind == a['kind'],
                (client_agreements.c.signed_on == a['signed_on']) if a['signed_on'] else client_agreements.c.signed_on.is_(None),
                client_agreements.c.reference == a['reference'][:120]).limit(1)).first()
            if key in seen or on_file:
                already += 1
                continue
            seen.add(key)
        elif it['type'] == 'file':
            if c.execute(select(client_files.c.id).where(client_files.c.client_id == cid, client_files.c.sha256 == sha_of.get(it['file']['id'], '')).limit(1)).first():
                already += 1
                continue
        if it['type'] == 'file':
            file_id = db.uid()
            note = f"Signed before this portal ({records.SOURCES.get(a['source'], 'Other') if a else 'imported'})"[:1000]
            c.execute(insert(client_files).from_select(
                ['id', 'client_id', 'name', 'content_type', 'size', 'sha256', 'scan', 'category', 'note', 'body', 'uploaded_by', 'created'],
                select(literal(file_id), literal(cid), client_import_files.c.name, literal('application/pdf'), client_import_files.c.size,
                       client_import_files.c.sha256, client_import_files.c.scan, literal('contract'), literal(note), client_import_files.c.body,
                       literal(actor['id']), literal(t)).where(client_import_files.c.id == it['file']['id'])))
            stored += 1
            db.log(c, actor, 'client.file_uploaded', cid, {'document': it['file']['name'], 'scan': it['file']['scan'], 'category': 'contract', 'source': 'import'})
        if not a:
            continue
        row = {'id': db.uid(), 'client_id': cid, 'entity': a['entity'], 'kind': a['kind'], 'title': records.KINDS[a['kind']], 'source': a['source'],
               'signed_on': a['signed_on'] or None, 'effective_on': a['effective_on'] or None, 'client_signer': a['client_signer'][:200], 'company_signer': '',
               'reference': a['reference'][:120], 'notes': a['notes'][:4000], 'file_id': file_id, 'created': t, 'created_by': actor['id'], 'updated': t,
               'test': bool(clients_now[cid]['test'])}
        c.execute(client_agreements.insert().values(**row))
        db.log(c, actor, 'client.past_agreement_added', cid, records.log_detail(clients_now[cid], row) | {'via': 'import'})
        agreements += 1
    for cid in {created.get(it['match'].get('new_key')) or (it['match'].get('client') or {}).get('id') for it in live} - {None}:
        c.execute(update(db.clients).where(db.clients.c.id == cid).values(updated=t))
    c.execute(delete(client_import_files).where(client_import_files.c.import_id == session['id']))
    c.execute(delete(client_imports).where(client_imports.c.id == session['id']))
    summary = {'clients_created': len(set(created.values())), 'agreements': agreements, 'files': stored, 'details_filled': filled, 'skipped': v['counts']['skip'],
               'already_on_file': already}
    db.log(c, actor, 'client.import_committed', detail=summary)
    return summary


def details_of(rows: list[dict]) -> dict:
    out = {}
    for r in rows:
        for k in ('address', 'npi', 'email'):
            if r.get(k) and not out.get(k):
                out[k] = r[k]
    if out.get('npi') and not re.fullmatch(r'\d{10}', out['npi'].replace(' ', '')):
        out.pop('npi')
    elif out.get('npi'):
        out['npi'] = out['npi'].replace(' ', '')
    if out.get('email') and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', out['email']):
        out.pop('email')
    if out.get('address'):
        out['address'] = out['address'][:500]
    return out


def fill_details(c, actor: dict, client_id: str, details: dict, t: float) -> int:
    row = c.execute(select(db.clients.c.address, db.clients.c.profile, db.clients.c.draft).where(db.clients.c.id == client_id)).mappings().first()
    if not row or not details:
        return 0
    values, fields = {}, []
    profile = dict(row['profile'] or {})
    if details.get('address') and not (row['address'] or '').strip():
        values['address'] = details['address']
        values['draft'] = dict(row['draft'] or {}, address=details['address'])
        fields.append('address')
    for k in ('npi', 'email'):
        if details.get(k) and not profile.get(k):
            profile[k] = details[k]
            fields.append(k)
    if 'npi' in fields or 'email' in fields:
        values['profile'] = profile
    if not values:
        return 0
    c.execute(update(db.clients).where(db.clients.c.id == client_id).values(updated=t, **values))
    db.log(c, actor, 'client.updated', client_id, {'fields': fields, 'source': 'import'})
    return 1


# ------------------------------------------------------------------ routes
def decoded(payload: dict, limit: int, label: str) -> tuple[str, bytes]:
    if not isinstance(payload, dict):
        raise ValueError('Invalid request.')
    try:
        raw = base64.b64decode(str(payload.get('data', '')), validate=True)
    except (ValueError, TypeError):
        raise ValueError('The file could not be read.') from None
    if not 1 <= len(raw) <= limit:
        raise ValueError(f'Upload {label} smaller than {limit // 1_000_000} MB.')
    name = re.sub(r'[^\w .()&,-]', '_', esign.clean(payload.get('name'), 'File name', 200, True))
    return name, raw


def install(app) -> None:
    def actor_of(request, c):
        return sec.get_actor(request, c)

    @app.get('/api/client-imports/template.{fmt}')
    def api_template(fmt: str, request: Request):
        with db.engine.connect() as c:
            admin_only(actor_of(request, c))
        if fmt not in ('xlsx', 'csv'):
            raise HTTPException(404, 'Not found.')
        body, media, name = template(fmt)
        return Response(body, media_type=media, headers={'Content-Disposition': f'attachment; filename="{name}"'})

    @app.post('/api/client-imports')
    def api_start(request: Request, payload: dict = Body(default={})):
        """Open the person's import in progress, or start one (fresh: true starts over)."""
        with esign.write_tx() as c:
            a = actor_of(request, c)
            admin_only(a)
            purge(c)
            current = c.execute(select(client_imports.c.id).where(client_imports.c.owner_id == a['id']).order_by(client_imports.c.created.desc())).scalars().all()
            if payload.get('fresh') is True and current:
                c.execute(delete(client_import_files).where(client_import_files.c.import_id.in_(current)))
                c.execute(delete(client_imports).where(client_imports.c.id.in_(current)))
                current = []
            if current:
                session = session_for(c, a, current[0])
            else:
                t = time.time()
                session = {'id': db.uid(), 'owner_id': a['id'], 'created': t, 'updated': t, 'expires': t + KEEP_DAYS * 86400, 'sheet': None,
                           'choices': {'items': {}, 'defaults': {}}}
                c.execute(client_imports.insert().values(**session))
            return public_view(c, a, session)

    @app.get('/api/client-imports/{import_id}')
    def api_view(import_id: str, request: Request):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            return public_view(c, a, session_for(c, a, import_id))

    @app.post('/api/client-imports/{import_id}/sheet')
    def api_sheet(import_id: str, request: Request, payload: dict = Body(...)):
        with db.engine.connect() as c:
            session_for(c, actor_of(request, c), import_id)
        if payload.get('remove') is True:
            sheet = None
        else:
            name, raw = decoded(payload, SHEET_LIMIT, 'a spreadsheet')
            esign.scan_upload(raw)
            sheet = parse_sheet(name, raw)
        with esign.write_tx() as c:
            a = actor_of(request, c)
            session = session_for(c, a, import_id)
            touch(c, import_id, sheet=sheet)
            session['sheet'] = sheet
            return public_view(c, a, session)

    @app.post('/api/client-imports/{import_id}/files')
    def api_file(import_id: str, request: Request, payload: dict = Body(...)):
        with db.engine.connect() as c:
            session_for(c, actor_of(request, c), import_id)
            people = records.company_people(c)
        name, raw = decoded(payload, FILE_LIMIT, 'a file')
        if not name.lower().endswith('.pdf') or not raw.startswith(b'%PDF'):
            raise ValueError('Upload the signed agreements as PDF files.')
        verdict = esign.scan_upload(raw)
        found = pdf_facts.read(raw, list(ENTITIES.values()), people, name)
        sha = hashlib.sha256(raw).hexdigest()
        with esign.write_tx() as c:
            a = actor_of(request, c)
            session_for(c, a, import_id)
            count, same = c.execute(select(func.count(), func.sum(case((client_import_files.c.sha256 == sha, 1), else_=0)))
                                    .where(client_import_files.c.import_id == import_id)).one()
            if count >= MAX_FILES:
                raise HTTPException(400, f'An import holds up to {MAX_FILES} files. Import these first, then start another import.')
            if same:
                raise HTTPException(409, f'{name} was already added to this import.')
            row = {'id': db.uid(), 'import_id': import_id, 'name': name, 'size': len(raw), 'sha256': sha, 'scan': verdict, 'pages': found['pages'] or None,
                   'found': found, 'body': raw, 'created': time.time()}
            c.execute(client_import_files.insert().values(**row))
            touch(c, import_id)
            return {'id': row['id'], 'name': name, 'scan': verdict, 'found': pdf_facts.public(found)}

    @app.post('/api/client-imports/{import_id}/files/{file_id}/remove')
    def api_file_remove(import_id: str, file_id: str, request: Request):
        with esign.write_tx() as c:
            a = actor_of(request, c)
            session_for(c, a, import_id)
            c.execute(delete(client_import_files).where(client_import_files.c.id == file_id, client_import_files.c.import_id == import_id))
            touch(c, import_id)
            return {'ok': True}

    @app.post('/api/client-imports/{import_id}/choices')
    def api_choice(import_id: str, request: Request, payload: dict = Body(...)):
        """{key, client_id | new (a name) | skip: true | clear: true} for one item; or {defaults: {entity, kind, source}}."""
        with esign.write_tx() as c:
            a = actor_of(request, c)
            session = session_for(c, a, import_id)
            choices = session['choices']
            if isinstance(payload.get('defaults'), dict):
                d = payload['defaults']
                if d.get('entity'):
                    sec.ensure_company(a, d['entity'])
                choices['defaults'] = {'entity': d.get('entity') or '', 'kind': d.get('kind') if d.get('kind') in records.KINDS else 'baa',
                                       'source': d.get('source') if d.get('source') in records.PAST_SOURCES else 'manual'}
            else:
                key = str(payload.get('key', ''))[:40]
                if not key:
                    raise ValueError('Choose an item.')
                if payload.get('clear') is True:
                    choices['items'].pop(key, None)
                elif payload.get('skip') is True:
                    choices['items'][key] = {'skip': True}
                elif payload.get('client_id'):
                    client_profiles.client_for(c, a, str(payload['client_id']))
                    choices['items'][key] = {'client_id': str(payload['client_id'])}
                elif payload.get('client_number'):
                    num = (client_numbers.search_terms(str(payload['client_number'])) or [''])[0]
                    row = c.execute(select(db.clients.c.id).where(db.clients.c.client_number == num, sec.scope(a, db.clients.c.owner_id))).first()
                    if not row:
                        raise HTTPException(404, 'No client with that number.')
                    choices['items'][key] = {'client_id': row[0]}
                elif payload.get('new'):
                    choices['items'][key] = {'new': esign.clean(payload['new'], 'Client name', 200, True)}
                else:
                    raise ValueError('Choose a client, a new client or skip.')
            touch(c, import_id, choices=choices)
            session['choices'] = choices
            return public_view(c, a, session)

    @app.post('/api/client-imports/{import_id}/commit')
    def api_commit(import_id: str, request: Request):
        with esign.write_tx() as c:
            a = actor_of(request, c)
            return commit(c, a, session_for(c, a, import_id))

    @app.post('/api/client-imports/{import_id}/cancel')
    def api_cancel(import_id: str, request: Request):
        with esign.write_tx() as c:
            a = actor_of(request, c)
            session_for(c, a, import_id)
            c.execute(delete(client_import_files).where(client_import_files.c.import_id == import_id))
            c.execute(delete(client_imports).where(client_imports.c.id == import_id))
            db.log(c, a, 'client.import_cancelled')
            return {'ok': True}

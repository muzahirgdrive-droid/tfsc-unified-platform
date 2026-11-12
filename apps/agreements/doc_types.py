"""Admin > Agreement types: documents beyond the three built-in agreements.

The Business Associate Agreement, Services Agreement and Mutual NDA are built in and are never changed here (they can only
be hidden from the builder). A type added here takes its wording from a Word file. Headings become numbered sections and
places written like {Client name} are filled in from the builder; the agreement then goes through the same engine as the
built-in ones, so it gets the company's letterhead, margins, fonts, footer ("Page X of Y") and the signature block.

Stored in the settings table under ``doc_types``:
    {"hidden": ["nda", ...], "custom": {"x_ab12cd34": {title, prefix, description, companies, active, services,
                                                        blocks, fields, file_name, sha256, created, created_by}}}
An agreement generated from a type keeps a copy of the wording it was generated with, so a later change to the type never
changes an agreement already generated or signed.
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
import secrets
import time
import zipfile

from fastapi import Body, HTTPException, Request
from sqlalchemy import select

import database as db
import security as sec

KEY = 'doc_types'
BUILTIN = {'baa': ('Business Associate Agreement', 'BAA', 'Privacy, security and handling of protected health information.'),
           'services': ('Services Agreement', 'Services', 'Selected services, responsibilities, pricing and commercial terms.'),
           'nda': ('Mutual NDA', 'NDA', 'Confidentiality for information both parties share before or during the engagement.')}
# Places the builder already knows. Anything else in braces is asked for in the builder.
KNOWN = {'client name': 'client_name', 'client address': 'address', 'effective date': 'effective_date', 'governing state': 'governing_state',
         'company name': '_company', 'client signatory': 'client_signatory', 'client title': 'client_title', 'client email': 'client_email',
         'services and fees': '_services', 'agreement reference': '_reference'}
TYPE_KEY = re.compile(r'x_[a-f0-9]{8}')
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def read(c) -> dict:
    value = c.execute(select(db.settings.c.value).where(db.settings.c.key == KEY)).scalar()
    out = {'hidden': [], 'custom': {}}
    if isinstance(value, dict):
        out['hidden'] = [x for x in value.get('hidden', []) if x in BUILTIN]
        out['custom'] = {k: v for k, v in (value.get('custom') or {}).items() if TYPE_KEY.fullmatch(k) and isinstance(v, dict)}
    return out


def _save(c, value: dict) -> None:
    if c.execute(select(db.settings.c.key).where(db.settings.c.key == KEY)).first():
        c.execute(db.settings.update().where(db.settings.c.key == KEY).values(value=value))
    else:
        c.execute(db.settings.insert().values(key=KEY, value=value))


def for_builder(c, actor: dict) -> list[dict]:
    """The agreement types this person may choose, in builder order (the hidden built-ins left out)."""
    stored = read(c)
    out = [{'key': k, 'title': t, 'description': d, 'builtin': True, 'hidden': k in stored['hidden']} for k, (t, _, d) in BUILTIN.items()]
    for k, t in stored['custom'].items():
        if not t.get('active'):
            continue
        companies = t.get('companies') or None
        if actor['role'] != 'super_admin' and companies and not set(companies) & set(actor['companies']):
            continue
        out.append({'key': k, 'title': t['title'], 'description': t.get('description', ''), 'builtin': False, 'companies': companies,
                    'services': bool(t.get('services')), 'fields': t.get('fields', []), 'symbol': t['title'][:1].upper()})
    return out


# ------------------------------------------------------------------ reading the Word file
def parse_docx(raw: bytes) -> list[dict]:
    """Blocks (title / h1 / h2 / p / bullet) from a Word document's paragraphs. Tables and pictures are not taken over."""
    from xml.etree import ElementTree as ET
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if z.getinfo('word/document.xml').file_size > 8_000_000:
                raise ValueError('The Word file is too large.')
            xml = z.read('word/document.xml')
    except (KeyError, zipfile.BadZipFile):
        raise ValueError('Upload a Word document (.docx).') from None
    root = ET.fromstring(xml)
    blocks = []
    for p in root.iter(W + 'p'):
        style = p.find(f'{W}pPr/{W}pStyle')
        style = (style.get(W + 'val') if style is not None else '').casefold()
        runs = [t.text or '' for t in p.iter(W + 't')]
        text = re.sub(r'\s+', ' ', ''.join(runs)).strip()
        if not text:
            continue
        bold = all((r.find(f'{W}rPr/{W}b') is not None) for r in p.findall(W + 'r') if ''.join(t.text or '' for t in r.iter(W + 't')).strip())
        listed = p.find(f'{W}pPr/{W}numPr') is not None or 'list' in style
        if style == 'title' or (not blocks and bold and len(text) < 90):
            kind = 'title'
        elif style.startswith('heading1') or style == 'heading 1':
            kind = 'h1'
        elif style.startswith('heading'):
            kind = 'h2'
        elif listed:
            kind = 'bullet'
            text = re.sub(r'^[•\-–·]\s*', '', text)
        elif bold and len(text) < 90:
            kind = 'h1'
        else:
            kind = 'p'
        blocks.append({'type': kind, 'text': text})
    if not any(b['type'] in ('p', 'bullet') for b in blocks):
        raise ValueError('No wording was found in the Word file.')
    if len(blocks) > 800:
        raise ValueError('The Word file has more than 800 paragraphs. Split it into shorter agreements.')
    return blocks


def placeholders(blocks: list[dict]) -> tuple[list[str], list[dict]]:
    """(known places found, fields the builder must ask for)."""
    known, asked = [], []
    for b in blocks:
        for name in re.findall(r'\{([^{}]{1,60})\}', b['text']):
            label = name.strip()
            if label.casefold() in KNOWN:
                if label not in known:
                    known.append(label)
            elif label.casefold() not in [f['label'].casefold() for f in asked]:
                asked.append({'key': re.sub(r'[^a-z0-9]+', '_', label.casefold()).strip('_')[:30] or f'f{len(asked)}', 'label': label})
    return known, asked


# ------------------------------------------------------------------ generating
def build(type_def: dict, data: dict, contract_id: str) -> list[dict]:
    from entities import get_entity
    import contract_content as original
    entity = get_entity(data.get('entity'))
    import datetime as dt
    effective = dt.date.fromisoformat(data['effective_date'])
    values = {'client_name': data['client_name'], 'address': data.get('address', ''), 'effective_date': f"{effective.strftime('%B')} {effective.day}, {effective.year}",
              'governing_state': data.get('governing_state') or entity['governing_state'], '_company': entity['name'], 'client_signatory': data.get('client_signatory', ''),
              'client_title': data.get('client_title', ''), 'client_email': data.get('client_email', ''), '_reference': contract_id}
    asked = (data.get('custom_fields') or {}).get(type_def['_key'], {})
    by_label = {f['label'].casefold(): asked.get(f['key'], '') for f in type_def.get('fields', [])}
    out, h1, h2 = [], 0, 0

    def fill(text):
        def one(m):
            label = m.group(1).strip().casefold()
            if label in KNOWN:
                return str(values.get(KNOWN[label], '')) if KNOWN[label] != '_services' else m.group(0)
            return str(by_label.get(label, '')) or '________'
        return re.sub(r'\{([^{}]{1,60})\}', one, text)
    for b in type_def['blocks']:
        text = b['text']
        if re.fullmatch(r'\{\s*services and fees\s*\}', text, flags=re.I):
            out.extend(services_table(data))
            continue
        text = fill(text)
        if b['type'] == 'h1':
            h1 += 1
            h2 = 0
            text = re.sub(r'^(\d+(\.\d+)*\.?|[A-Z]\.|Article\s+[IVXLC\d]+[.:]?)\s+', '', text)
            text = f'{h1}. {text}'
        elif b['type'] == 'h2':
            h2 += 1
            text = re.sub(r'^\d+(\.\d+)*\.?\s+', '', text)
            text = f'{h1}.{h2} {text}' if h1 else text
        out.append(original.block(b['type'], text))
    if not out or out[0]['type'] != 'title':
        out.insert(0, original.block('title', type_def['title'].upper()))
    out.append(original.block('signature', f"The undersigned agree to this {type_def['title']}, reference {contract_id}, effective {values['effective_date']}, "
                                           f"between {entity['name']} and {data['client_name']}."))
    return out


def services_table(data: dict) -> list[dict]:
    import contract_content as original
    from revised_content import fee_rows
    rows = []
    for s in data.get('services', []) + data.get('custom_services', []):
        rows += fee_rows(original._service_record(s))
    if not rows:
        return [original.block('p', 'No services were selected.')]
    return [{'type': 'table', 'headers': ['Service', 'Fee (USD, unless stated as a percentage)'], 'rows': rows}]


# ------------------------------------------------------------------ admin screens
def install(app) -> None:
    def super_admin(request, c):
        a = sec.get_actor(request, c)
        if a['role'] != 'super_admin':
            raise HTTPException(403, 'Only the Super Admin can change agreement types.')
        return a

    def view(t, key):
        return {'key': key, 'title': t['title'], 'prefix': t.get('prefix', ''), 'description': t.get('description', ''), 'companies': t.get('companies') or [],
                'active': bool(t.get('active')), 'services': bool(t.get('services')), 'fields': t.get('fields', []), 'known': t.get('known', []),
                'file_name': t.get('file_name', ''), 'sections': sum(1 for b in t['blocks'] if b['type'] == 'h1'), 'paragraphs': len(t['blocks']),
                'updated': t.get('updated') or t.get('created')}

    @app.get('/api/admin/doc-types')
    def api_list(request: Request):
        with db.engine.connect() as c:
            super_admin(request, c)
            stored = read(c)
            return {'builtin': [{'key': k, 'title': t, 'description': d, 'hidden': k in stored['hidden']} for k, (t, _, d) in BUILTIN.items()],
                    'custom': [view(t, k) for k, t in stored['custom'].items()]}

    @app.post('/api/admin/doc-types/builtin/{key}')
    def api_builtin(key: str, request: Request, payload: dict = Body(...)):
        if key not in BUILTIN:
            raise HTTPException(404, 'Unknown agreement type.')
        with db.engine.begin() as c:
            a = super_admin(request, c)
            stored = read(c)
            hidden = set(stored['hidden'])
            hidden.discard(key) if payload.get('hidden') is not True else hidden.add(key)
            if not set(BUILTIN) - hidden and not any(t.get('active') for t in stored['custom'].values()):
                raise ValueError('At least one agreement type must stay available in the builder.')
            stored['hidden'] = sorted(hidden)
            _save(c, stored)
            db.log(c, a, 'doc_type.builtin_visibility', detail={'type': key, 'hidden': key in hidden})
            return {'ok': True}

    @app.post('/api/admin/doc-types')
    def api_save(request: Request, payload: dict = Body(...)):
        import server as legacy
        from entities import ENTITIES
        with db.engine.begin() as c:
            a = super_admin(request, c)
            stored = read(c)
            key = payload.get('key') or None
            if key and key not in stored['custom']:
                raise HTTPException(404, 'Agreement type not found.')
            current = stored['custom'].get(key) or {}
            title = legacy.clean(payload.get('title'), 'Name', True, 120, typography=False)
            title = re.sub(r'[\\/:*?"<>|]+', '-', title).strip(' .-')
            if title.casefold() in [t.casefold() for t, _, _ in BUILTIN.values()] + [x['title'].casefold() for k, x in stored['custom'].items() if k != key]:
                raise HTTPException(409, 'An agreement type with this name already exists.')
            prefix = re.sub(r'[^A-Za-z0-9]', '', str(payload.get('prefix') or ''))[:10] or re.sub(r'[^A-Za-z]', '', title)[:3].upper() or 'DOC'
            companies = [x for x in (payload.get('companies') or []) if x in ENTITIES]
            item = dict(current, title=title, prefix=prefix, description=legacy.clean(payload.get('description'), 'Description', False, 300),
                        companies=companies, active=payload.get('active') is True, services=payload.get('services') is True, updated=time.time())
            if payload.get('file'):
                raw = base64.b64decode(str(payload['file']).split(',')[-1], validate=False)
                if len(raw) > 10_000_000:
                    raise ValueError('The Word file must be smaller than 10 MB.')
                blocks = parse_docx(raw)
                known, asked = placeholders(blocks)
                item.update(blocks=blocks, known=known, fields=asked, file_name=legacy.clean(payload.get('file_name') or 'wording.docx', 'File name', False, 150, typography=False),
                            sha256=hashlib.sha256(raw).hexdigest())
            if not item.get('blocks'):
                raise ValueError('Upload the wording as a Word file (.docx).')
            if not key:
                key = 'x_' + secrets.token_hex(4)
                item.update(created=time.time(), created_by=a['id'])
            stored['custom'][key] = item
            _save(c, stored)
            db.log(c, a, 'doc_type.saved', detail={'type': key, 'title': title, 'active': item['active'], 'file': item.get('sha256', '')[:16]})
            return view(item, key)

    @app.post('/api/admin/doc-types/{key}/preview')
    def api_preview(key: str, request: Request, payload: dict = Body(default={})):
        """The type's wording as an agreement for the chosen company, with sample client details (PDF)."""
        import companies as comp
        import server as legacy
        from document_engine import convert_to_pdf, generate_docx
        from entities import ENTITIES
        from fastapi.responses import Response
        import tempfile
        from pathlib import Path
        with db.engine.connect() as c:
            super_admin(request, c)
            t = read(c)['custom'].get(key)
            if not t:
                raise HTTPException(404, 'Agreement type not found.')
            entity = payload.get('entity') if payload.get('entity') in ENTITIES else (t.get('companies') or list(ENTITIES))[0]
            company = comp.profile(c, entity, with_image=False)
        data = {'client_name': 'Sample Clinic LLC', 'address': '100 Example Way, Las Vegas, NV 89101', 'effective_date': time.strftime('%Y-%m-%d'),
                'entity': entity, 'client_signatory': 'Jane Example', 'client_title': 'Managing Member', 'services': [], 'custom_services': [],
                '_signatory': company['signatory'], '_signatory_title': company['title']}
        blocks = build(dict(t, _key=key), data, 'PREVIEW')
        with tempfile.TemporaryDirectory(prefix='tfsc-render-') as work:
            word, pdf = Path(work) / 'preview.docx', Path(work) / 'preview.pdf'
            generate_docx(blocks, data, 'PREVIEW', word)
            convert_to_pdf(word, pdf)
            body = pdf.read_bytes()
        return Response(body, media_type='application/pdf', headers={'Content-Disposition': 'inline; filename="Preview.pdf"', 'X-TFSC-Frame': 'self'})

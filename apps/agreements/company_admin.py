"""Add and edit contracting companies from the app (Super Admin only).

A company carries its letterhead (a .docx whose logo/artwork is in the Word header or footer), its address,
governing law, authorized signatory and its own list of services. Agreements for the company generate on that
letterhead with its name, address and governing law. The letterhead, logo and signature are kept in the database
so backups include them. Page geometry (margins, text width) is stored in twips (dxa) and can be adjusted and
previewed before saving, because a letterhead's own margins do not always clear its artwork.
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
import time
import zipfile

from fastapi import Body, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select

import database as db
import entities as entmod
import security as sec
import companies as comp
from entities import get_entity

LETTERHEAD_LIMIT = 8_000_000
LOGO_LIMIT = 4_000_000
BUILTIN_KEYS = {'scribe', 'ehs', '24-7'}
DOC_TYPES = ('baa', 'services', 'nda')
HEX = re.compile(r'#[0-9A-Fa-f]{6}')
# Pricing models offered for each billing type (same wording the standard catalogue uses).
RECURRING_MODELS = ['percentage', 'fte', 'pte', 'unit', 'fixed', 'consultancy']
ONDEMAND_MODELS = ['one_time', 'two_installments']


def _clean(value, label, limit, required=False):
    return comp.sec_clean(value, label, limit, required)


def _color(value, label, default):
    value = (str(value or '').strip() or default)
    if not HEX.fullmatch(value):
        raise ValueError(f'{label} must be a colour like #1D4277.')
    return value.upper()


# ------------------------------------------------------------------ letterhead
def parse_letterhead(raw: bytes) -> dict:
    """Page size and margins (twips) read from an uploaded .docx, with the text width derived from them.

    The letterhead must keep its logo/artwork in the Word header or footer: the agreement text replaces the body.
    """
    if not 1 <= len(raw) <= LETTERHEAD_LIMIT:
        raise ValueError('Choose a Word letterhead (.docx) smaller than 8 MB.')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
            if 'word/document.xml' not in names:
                raise ValueError
            document = z.read('word/document.xml').decode('utf-8', 'replace')
    except (zipfile.BadZipFile, ValueError, KeyError):
        raise ValueError('That file is not a Word .docx letterhead. Save it from Word as .docx and try again.') from None
    headers = [n for n in names if re.fullmatch(r'word/header\d+\.xml', n)]
    footers = [n for n in names if re.fullmatch(r'word/footer\d+\.xml', n)]
    if not headers and not footers:
        raise ValueError('The letterhead has no header or footer. Put the company logo and artwork in the Word header (Insert > Header) so the agreement text can be placed on it.')
    sections = re.findall(r'<w:sectPr\b.*?</w:sectPr>', document, re.S)
    if not sections:
        raise ValueError('The letterhead is missing its page setup. Open it in Word and save it again as .docx.')
    section = sections[-1]
    size = re.search(r'<w:pgSz\b[^>]*/>', section)
    margin = re.search(r'<w:pgMar\b[^>]*/>', section)
    if not size:
        raise ValueError('The letterhead is missing its page size. Open it in Word and save it again as .docx.')

    def attr(text, name, default=None):
        m = re.search(fr'w:{name}="(-?\d+)"', text or '')
        if m:
            return int(m.group(1))
        if default is None:
            raise ValueError('The letterhead page setup could not be read. Open it in Word and save it again as .docx.')
        return default

    page_w = attr(size.group(0), 'w')
    page_h = attr(size.group(0), 'h')
    mtext = margin.group(0) if margin else ''
    top, right, bottom, left = (attr(mtext, side, 1440) for side in ('top', 'right', 'bottom', 'left'))
    if not 9000 <= page_w <= 17000 or not 12000 <= page_h <= 25000:
        raise ValueError('The letterhead page size is unusual. Use a Letter or A4 letterhead.')
    for side, value in (('top', top), ('right', right), ('bottom', bottom), ('left', left)):
        if not 0 <= value <= page_w // 2:
            raise ValueError(f'The letterhead {side} margin is out of range. Set normal margins in Word and save again.')
    width = page_w - left - right
    if width < 2000:
        raise ValueError('The letterhead side margins leave too little room for text. Reduce them in Word.')
    return {'page_w': page_w, 'page_h': page_h, 'top': top, 'right': right, 'bottom': bottom, 'left': left,
            'width': width, 'sha256': hashlib.sha256(raw).hexdigest(), 'headers': len(headers), 'footers': len(footers),
            'bytes': raw}


def _geometry(payload: dict, parsed: dict) -> dict:
    """Final page geometry: the admin may override the parsed margins (to clear tall artwork)."""
    out = {'width': parsed['width'], 'top': parsed['top'], 'bottom': parsed['bottom'], 'left': parsed['left'], 'right': parsed['right']}
    margins = payload.get('margins') or {}
    for side in ('top', 'bottom', 'left', 'right'):
        if margins.get(side) is not None:
            try:
                twips = int(round(float(margins[side]) * 1440))   # the form sends inches
            except (TypeError, ValueError):
                raise ValueError(f'The {side} margin must be a number of inches.') from None
            if not 0 <= twips <= parsed['page_w'] // 2:
                raise ValueError(f'The {side} margin is out of range.')
            out[side] = twips
    out['width'] = parsed['page_w'] - out['left'] - out['right']
    if out['width'] < 2000:
        raise ValueError('The side margins leave too little room for text.')
    return out


# ------------------------------------------------------------------ logo
def validate_logo(value) -> tuple[bytes, str]:
    from PIL import Image
    raw = comp.decode_image(value)
    if len(raw) > LOGO_LIMIT:
        raise ValueError('Choose a logo image smaller than 4 MB.')
    try:
        image = Image.open(io.BytesIO(raw))
        if image.format not in ('PNG', 'JPEG', 'WEBP'):
            raise ValueError
        image.load()
    except Exception:
        raise ValueError('Choose a PNG or JPEG logo image.') from None
    image = image.convert('RGBA')
    image.thumbnail((900, 300))
    out = io.BytesIO()
    image.save(out, 'PNG', optimize=True)
    return out.getvalue(), 'png'


def make_logo(short: str, color: str) -> tuple[bytes, str]:
    """A simple wordmark PNG used when no logo image is supplied, so emails and the signing page still show a mark."""
    from PIL import Image, ImageDraw, ImageFont
    text = (short or 'Company').strip()[:28]
    try:
        font = ImageFont.truetype(str(db.APP / 'assets' / 'fonts' / 'Poppins-SemiBold.ttf'), 72)
    except Exception:
        font = ImageFont.load_default()
    scratch = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    box = scratch.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0] + 24, box[3] - box[1] + 24
    image = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(image).text((12 - box[0], 12 - box[1]), text, font=font, fill=color)
    out = io.BytesIO()
    image.save(out, 'PNG', optimize=True)
    return out.getvalue(), 'png'


# ------------------------------------------------------------------ services
def _slug(value: str, fallback: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '_', (value or '').lower()).strip('_')
    return s[:36] or fallback


def normalize_services(raw) -> list:
    """The company's services, shaped like the standard catalogue so the builder treats them the same way."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 60:
        raise ValueError('Add no more than 60 services.')
    from contract_content import MODELS
    out, used = [], set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError('Each service must have a name.')
        name = _clean(item.get('name'), 'Service name', 160, True)
        category = item.get('category') or item.get('type') or 'on_demand'
        if category not in ('recurring', 'on_demand'):
            raise ValueError('Each service must be recurring or on demand.')
        model_ids = RECURRING_MODELS if category == 'recurring' else ONDEMAND_MODELS
        sid = _slug(name, f'service_{index}')
        while sid in used:
            sid += 'x'
        used.add(sid)
        out.append({'id': sid, 'name': name, 'category': category,
                    'models': [{'id': m, 'label': MODELS[m]} for m in model_ids],
                    'description': _clean(item.get('description'), 'Service summary', 400),
                    'scope': _clean(item.get('scope'), 'Service scope', 2500),
                    'responsibilities': _clean(item.get('responsibilities'), 'Service responsibilities', 2500),
                    'exclusions': _clean(item.get('exclusions'), 'Service exclusions', 2500)})
    return out


def _doc_types(raw) -> list:
    if raw is None:
        return list(DOC_TYPES)
    if not isinstance(raw, list) or any(x not in DOC_TYPES for x in raw):
        raise ValueError('Choose which agreement types the company can produce.')
    types = [t for t in DOC_TYPES if t in raw]
    if not types:
        raise ValueError('Enable at least one agreement type (BAA, Services Agreement or Mutual NDA).')
    return types


# ------------------------------------------------------------------ build the row
def _identity(payload: dict, existing_name: str | None = None) -> dict:
    name = _clean(payload.get('name'), 'Company legal name', 200, True).upper()
    short = _clean(payload.get('short'), 'Short name', 120, True)
    display = _clean(payload.get('display'), 'Display name', 200) or name.title()
    address = _clean(payload.get('address'), 'Registered address', 500, True)
    email = _clean(payload.get('email'), 'Notice email', 200)
    if email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise ValueError('Enter a valid notice email address or leave it blank.')
    state = _clean(payload.get('governing_state'), 'Governing law (state)', 60, True)
    import server
    state = next((x for x in server.STATES if x.casefold() == state.casefold()), None)
    if not state:
        raise ValueError('Use the full name of a US state or the District of Columbia for governing law.')
    timezone = _clean(payload.get('timezone'), 'Time zone', 60, True)
    import zoneinfo
    try:
        zoneinfo.ZoneInfo(timezone)
    except Exception:
        raise ValueError('Choose a valid time zone, for example America/New_York.') from None
    signatory = _clean(payload.get('signatory'), 'Authorized signatory', 150, True)
    title = _clean(payload.get('title'), 'Signatory title', 150, True)
    scale = payload.get('signature_scale', 0.85)
    try:
        scale = round(float(scale), 2)
    except (TypeError, ValueError):
        raise ValueError('Choose a signature size.') from None
    if not comp.SCALE_RANGE[0] <= scale <= comp.SCALE_RANGE[1]:
        raise ValueError('Choose a signature size between 30 % and 100 %.')
    return {'name': name, 'short': short, 'display': display, 'address': address, 'email': email,
            'governing_state': state, 'timezone': timezone, 'signatory': signatory, 'title': title,
            'signature_scale': scale,
            'color': _color(payload.get('color'), 'Brand colour', '#1D4277'),
            'accent': _color(payload.get('accent'), 'Accent colour', '#189BD2'),
            'dark': _color(payload.get('dark') or payload.get('color'), 'Header colour', '#0F2747'),
            'services': normalize_services(payload.get('services')),
            'doc_types': _doc_types(payload.get('doc_types'))}


def _unique_key(c, short: str, name: str) -> str:
    base = _slug(short or name, 'company')
    if base in BUILTIN_KEYS:
        base = 'co_' + base
    existing = {r[0] for r in c.execute(select(db.companies.c.key)).all()} | BUILTIN_KEYS
    key, n = base, 2
    while key in existing:
        key, n = f'{base}-{n}', n + 1
    return key


def transient_entity(identity: dict, geometry: dict, letterhead_path, logo_path=None) -> dict:
    """An entity dict for previewing an agreement before the company is saved."""
    return dict(key='preview', name=identity['name'], short=identity['short'], display=identity['display'],
                dark=identity['dark'], signatory=identity['signatory'], title=identity['title'],
                signature_scale=identity['signature_scale'], governing_state=identity['governing_state'],
                timezone=identity['timezone'], color=identity['color'], accent=identity['accent'],
                address=identity['address'], email=identity['email'], services=identity['services'],
                doc_types=identity['doc_types'], signature='preview-none.png', logo='preview.png',
                template='', template_path=letterhead_path, sha256=hashlib.sha256(letterhead_path.read_bytes()).hexdigest(),
                logo_path=logo_path, logo_url='', custom=True,
                width=geometry['width'], top=geometry['top'], bottom=geometry['bottom'], left=geometry['left'], right=geometry['right'])


# ------------------------------------------------------------------ create / update
def create_company(actor: dict, payload: dict) -> dict:
    identity = _identity(payload)
    if not payload.get('letterhead'):
        raise ValueError('Upload the company letterhead (.docx).')
    parsed = parse_letterhead(comp.decode_image(payload['letterhead']))
    geometry = _geometry(payload, parsed)
    logo_bytes, logo_ext = (validate_logo(payload['logo']) if payload.get('logo') else make_logo(identity['short'], identity['color']))
    now = time.time()
    with db.engine.begin() as c:
        if c.execute(select(db.companies.c.key).where(db.companies.c.name == identity['name'])).first() or identity['name'] in entmod._BUILTINS:
            raise ValueError('A company with that legal name already exists.')
        key = _unique_key(c, identity['short'], identity['name'])
        c.execute(db.companies.insert().values(key=key, letterhead=parsed['bytes'], letterhead_sha256=parsed['sha256'],
                  logo=logo_bytes, logo_ext=logo_ext, width=geometry['width'], top=geometry['top'], bottom=geometry['bottom'],
                  left=geometry['left'], right=geometry['right'], active=True, created=now, created_by=actor['id'], updated=now,
                  **identity))
        db.log(c, actor, 'company.created', detail={'company': identity['name'], 'key': key, 'services': len(identity['services'])})
    entmod.invalidate()
    if payload.get('signature'):
        with db.engine.begin() as c:
            comp.save_profile(c, actor, get_entity(identity['name']), {'signatory': identity['signatory'], 'title': identity['title'],
                              'signature': payload['signature'], 'signature_kind': payload.get('signature_kind', 'uploaded'),
                              'signature_scale': identity['signature_scale']})
    return _public(key)


def update_company(actor: dict, key: str, payload: dict) -> dict:
    with db.engine.connect() as c:
        row = c.execute(select(db.companies).where(db.companies.c.key == key)).mappings().first()
    if not row:
        raise HTTPException(404, 'Company not found.')
    identity = _identity(payload, existing_name=row['name'])
    values = dict(identity, updated=time.time())
    if payload.get('letterhead'):
        parsed = parse_letterhead(comp.decode_image(payload['letterhead']))
        geometry = _geometry(payload, parsed)
        values.update(letterhead=parsed['bytes'], letterhead_sha256=parsed['sha256'], **{k: geometry[k] for k in ('width', 'top', 'bottom', 'left', 'right')})
    elif payload.get('margins'):
        parsed = {'page_w': row['left'] + row['right'] + row['width'], 'top': row['top'], 'bottom': row['bottom'], 'left': row['left'], 'right': row['right']}
        values.update(**_geometry(payload, parsed))
    if payload.get('logo'):
        logo_bytes, logo_ext = validate_logo(payload['logo'])
        values.update(logo=logo_bytes, logo_ext=logo_ext)
    with db.engine.begin() as c:
        if c.execute(select(db.companies.c.key).where(db.companies.c.name == identity['name'], db.companies.c.key != key)).first() or (identity['name'] != row['name'] and identity['name'] in entmod._BUILTINS):
            raise ValueError('A company with that legal name already exists.')
        c.execute(db.companies.update().where(db.companies.c.key == key).values(**values))
        db.log(c, actor, 'company.updated', detail={'company': identity['name'], 'key': key})
    entmod.invalidate()
    if payload.get('signature') or payload.get('remove_signature'):
        with db.engine.begin() as c:
            comp.save_profile(c, actor, get_entity(identity['name']), {k: payload[k] for k in ('signature', 'signature_kind', 'remove_signature', 'signature_scale') if k in payload} | {'signatory': identity['signatory'], 'title': identity['title']})
    return _public(key)


def set_active(actor: dict, key: str, active: bool) -> dict:
    with db.engine.begin() as c:
        row = c.execute(select(db.companies).where(db.companies.c.key == key)).mappings().first()
        if not row:
            raise HTTPException(404, 'Company not found.')
        c.execute(db.companies.update().where(db.companies.c.key == key).values(active=active, updated=time.time()))
        db.log(c, actor, 'company.updated', detail={'company': row['name'], 'key': key, 'active': active})
    entmod.invalidate()
    return {'key': key, 'active': active}


def _public(key: str) -> dict:
    """Full editable detail of one custom company (letterhead/logo bytes excluded)."""
    with db.engine.connect() as c:
        row = c.execute(select(db.companies).where(db.companies.c.key == key)).mappings().first()
        if not row:
            raise HTTPException(404, 'Company not found.')
        p = comp.profile(c, row['name'], with_image=False)
    return {'key': row['key'], 'name': row['name'], 'short': row['short'], 'display': row['display'], 'address': row['address'],
            'email': row['email'], 'governing_state': row['governing_state'], 'timezone': row['timezone'],
            'color': row['color'], 'accent': row['accent'], 'dark': row['dark'], 'signatory': row['signatory'], 'title': row['title'],
            'signature_scale': row['signature_scale'], 'services': row['services'], 'doc_types': row['doc_types'],
            'active': row['active'], 'custom': True, 'has_signature': p['source'] is not None,
            'margins': {k: round(row[k] / 1440, 2) for k in ('top', 'bottom', 'left', 'right')}, 'width': row['width']}


def all_service_ids() -> set:
    """Every service id a user could be permitted: the standard catalogue plus every company's own services."""
    import server
    ids = {s['id'] for s in server.CATALOG}
    for e in entmod.ENTITIES.values():
        for s in (e.get('services') or []):
            ids.add(s['id'])
    return ids


def clean_user_services(value) -> list | None:
    """Normalise a user's allowed-services list. None / empty means every service is allowed."""
    if value is None or value == []:
        return None
    if not isinstance(value, list) or len(value) > 500:
        raise ValueError('Invalid service permissions.')
    known = all_service_ids()
    return [s for s in dict.fromkeys(str(x) for x in value) if s in known] or None


def list_rows() -> list:
    with db.engine.connect() as c:
        rows = c.execute(select(db.companies).order_by(db.companies.c.created, db.companies.c.key)).mappings().all()
    return [{'key': r['key'], 'name': r['name'], 'short': r['short'], 'governing_state': r['governing_state'],
             'services': len(r['services'] or []), 'active': r['active'], 'logo': f"/api/companies/{r['key']}/logo.png"} for r in rows]


# ------------------------------------------------------------------ preview
def _sample_blocks(identity: dict) -> list:
    name = identity['name']
    svc = (identity['services'] or [{}])[0]
    return [
        {'type': 'title', 'text': 'Services Agreement'},
        {'type': 'p', 'text': f'This sample is generated on the {name} letterhead to confirm the margins, logo and signature placement before the company is saved. Replace it with a real agreement once the layout looks right.'},
        {'type': 'h1', 'text': '1. Services'},
        {'type': 'p', 'text': svc.get('scope') or 'The services selected for an agreement appear here, each with its own scope, responsibilities and exclusions as defined for this company.'},
        {'type': 'bullet', 'text': 'The agreement text sits inside the page margins shown.'},
        {'type': 'bullet', 'text': 'The letterhead logo and footer come from the uploaded Word file.'},
        {'type': 'h1', 'text': '2. Governing law'},
        {'type': 'p', 'text': f"This Agreement is governed by the laws of the State of {identity['governing_state']}."},
        {'type': 'signature', 'text': 'The undersigned agree to the foregoing sample Agreement.'},
    ]


def preview_pdf(payload: dict) -> bytes:
    import tempfile
    import document_engine as engine
    identity = _identity(payload)
    if not payload.get('letterhead'):
        raise ValueError('Upload the letterhead to preview a sample.')
    parsed = parse_letterhead(comp.decode_image(payload['letterhead']))
    geometry = _geometry(payload, parsed)
    with tempfile.TemporaryDirectory(prefix='tfsc-preview-') as tmp:
        from pathlib import Path
        lh = Path(tmp) / 'letterhead.docx'
        lh.write_bytes(parsed['bytes'])
        logo_path = None
        if payload.get('logo'):
            logo_bytes, _ = validate_logo(payload['logo'])
            logo_path = Path(tmp) / 'logo.png'
            logo_path.write_bytes(logo_bytes)
        entity = transient_entity(identity, geometry, lh, logo_path)
        data = {'entity': identity['name'], 'client_name': 'Sample Client LLC', 'effective_date': time.strftime('%Y-%m-%d'),
                '_signatory': identity['signatory'], '_signatory_title': identity['title']}
        docx = Path(tmp) / 'sample.docx'
        engine.generate_docx(_sample_blocks(identity), data, 'SAMPLE-PREVIEW', docx, entity=entity)
        pdf = Path(tmp) / 'sample.pdf'
        engine.convert_to_pdf(docx, pdf)
        return pdf.read_bytes()


# ------------------------------------------------------------------ routes
def install(app) -> None:
    def super_admin(request, c):
        a = sec.get_actor(request, c)
        if a['role'] != 'super_admin':
            raise HTTPException(403, 'Only the Super Admin can add or change companies.')
        return a

    @app.post('/api/admin/companies/parse-letterhead')
    def api_parse_letterhead(request: Request, payload: dict = Body(...)):
        with db.engine.connect() as c:
            super_admin(request, c)
        parsed = parse_letterhead(comp.decode_image(payload.get('letterhead')))
        return {'page_width': round(parsed['page_w'] / 1440, 2), 'page_height': round(parsed['page_h'] / 1440, 2),
                'margins': {k: round(parsed[k] / 1440, 2) for k in ('top', 'bottom', 'left', 'right')},
                'headers': parsed['headers'], 'footers': parsed['footers']}

    @app.post('/api/admin/companies/preview')
    def api_preview(request: Request, payload: dict = Body(...)):
        with db.engine.connect() as c:
            super_admin(request, c)
        pdf = preview_pdf(payload)
        return {'pdf': 'data:application/pdf;base64,' + base64.b64encode(pdf).decode()}

    @app.post('/api/admin/companies/new')
    def api_create(request: Request, payload: dict = Body(...)):
        with db.engine.connect() as c:
            a = super_admin(request, c)
        return {'company': create_company(a, payload)}

    @app.get('/api/admin/companies/custom')
    def api_list_custom(request: Request):
        with db.engine.connect() as c:
            super_admin(request, c)
        return {'companies': list_rows()}

    @app.get('/api/admin/companies/custom/{key}')
    def api_get_custom(key: str, request: Request):
        with db.engine.connect() as c:
            super_admin(request, c)
        return {'company': _public(key)}

    @app.post('/api/admin/companies/custom/{key}')
    def api_update(key: str, request: Request, payload: dict = Body(...)):
        with db.engine.connect() as c:
            a = super_admin(request, c)
        return {'company': update_company(a, key, payload)}

    @app.post('/api/admin/companies/custom/{key}/active')
    def api_set_active(key: str, request: Request, payload: dict = Body(...)):
        with db.engine.connect() as c:
            a = super_admin(request, c)
        return set_active(a, key, bool(payload.get('active', True)))

    @app.get('/api/companies/{key}/logo.png')
    def api_logo(key: str, request: Request):
        with db.engine.connect() as c:
            sec.get_actor(request, c)
            row = c.execute(select(db.companies.c.logo, db.companies.c.logo_ext).where(db.companies.c.key == key)).first()
        if not row or not row[0]:
            raise HTTPException(404, 'No logo for this company.')
        data = bytes(row[0])
        kind = 'image/png' if (row[1] or 'png') == 'png' else 'image/jpeg'
        return Response(data, media_type=kind, headers={'Cache-Control': 'no-store'})

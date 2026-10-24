"""Company settings: who signs for each company, the signature they sign with, and workspace-wide settings.

The signature is kept in the database (so backups include it) as a trimmed PNG with a transparent background, made from
an uploaded image, a drawing or a typed name in a chosen style. Generated agreements use it on the company's signature
line. A company without a saved signature falls back to an image installed on the server with `agreements.sh
signatures`, and otherwise its signature line is left blank (the builder warns before generating).

Also here: the Email trust check, which reads a company domain's SPF, DKIM, DMARC and BIMI records over DNS-over-HTTPS
and explains what each one needs for inbox providers to show the company as verified.
"""
from __future__ import annotations

import base64
import io
import json
import re
import time

import httpx
from fastapi import Body, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select

import database as db
import security as sec
from entities import ENTITIES, get_entity, signature_path

SIGNATURE_KINDS = ('uploaded', 'drawn', 'typed')
UPLOAD_LIMIT = 5_000_000
WORKSPACE_DEFAULTS = {
    'portal_name': 'TFSC Agreements',
    'copyright_holder': 'TFSC Agreements',
    'help_email': 'muzahir@scribealign.com',
    # Shown on the signing page and in emails. Every phrase describes something the portal does; counsel reviews the wording.
    'trust_line': 'Encrypted connection · Tamper-evident audit trail · Built for HIPAA-regulated organizations',
}
WORKSPACE_LIMITS = {'portal_name': 60, 'copyright_holder': 120, 'help_email': 200, 'trust_line': 200}


def entity_by_key(key: str) -> dict:
    for entity in ENTITIES.values():
        if entity['key'] == key:
            return entity
    raise HTTPException(404, 'Company not found.')


# ------------------------------------------------------------------ signature images
def clean_signature(raw: bytes) -> bytes:
    """A signature image ready to print: the paper made transparent and the image trimmed to the ink.

    Images that already have transparency keep it; otherwise every pixel close to the paper's brightness becomes
    transparent and darker pixels keep their own colour (blue ink stays blue).
    """
    from PIL import Image, ImageOps
    if not 1 <= len(raw) <= UPLOAD_LIMIT:
        raise ValueError('Choose an image smaller than 5 MB.')
    try:
        image = Image.open(io.BytesIO(raw))
        if image.format not in ('PNG', 'JPEG', 'WEBP'):
            raise ValueError
        if image.width * image.height > 40_000_000:
            raise ValueError
        image.load()
    except Exception:
        raise ValueError('Choose a PNG or JPEG image of the signature.') from None
    image = ImageOps.exif_transpose(image).convert('RGBA')
    image.thumbnail((2400, 2400))
    alpha = image.getchannel('A')
    if alpha.getextrema()[0] == 255:
        gray = image.convert('L')
        histogram, seen, total = gray.histogram(), 0, image.width * image.height
        paper = 255
        for level in range(256):               # the paper is the brightness that 80 % of the pixels stay below
            seen += histogram[level]
            if seen >= total * 0.8:
                paper = level
                break
        edge = max(40, paper - 28)              # lighter than this is paper (or a shadow on it)
        ramp = max(1.0, edge * 0.45)
        alpha = gray.point([0 if v >= edge else min(255, int((edge - v) * 255 / ramp)) for v in range(256)])
        image.putalpha(alpha)
    box = alpha.point([0] * 25 + [255] * 231).getbbox()
    if not box:
        raise ValueError('No signature was found in the image. Use dark ink on light paper, or draw the signature instead.')
    pad = max(4, (box[3] - box[1]) // 20)
    image = image.crop((max(0, box[0] - pad), max(0, box[1] - pad), min(image.width, box[2] + pad), min(image.height, box[3] + pad)))
    image.thumbnail((1500, 500))
    out = io.BytesIO()
    image.save(out, 'PNG', optimize=True)
    return out.getvalue()


def decode_image(value) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError('Add a signature image.')
    if value.startswith('data:'):
        value = value.split(',', 1)[-1]
    try:
        return base64.b64decode(value, validate=True)
    except ValueError:
        raise ValueError('The signature image could not be read.') from None


# ------------------------------------------------------------------ company profiles
def installed_signature(entity: dict) -> tuple[bytes | None, list | None]:
    """An image installed on the server with `agreements.sh signatures` (and its crop), if there is one."""
    path = signature_path(db.DATA, entity['name'])
    if not path.is_file():
        return None, None
    crop = None
    meta = path.with_suffix('.json')
    if meta.is_file():
        try:
            crop = json.loads(meta.read_text()).get('crop')
        except ValueError:
            crop = None
    return path.read_bytes(), crop


def profile(c, entity_name: str, with_image: bool = True) -> dict:
    """Who signs for the company and with which signature: saved settings first, then the installed image."""
    entity = get_entity(entity_name)
    row = c.execute(select(db.company_profiles).where(db.company_profiles.c.entity == entity['name'])).mappings().first()
    out = {'entity': entity['name'], 'key': entity['key'], 'signatory': entity['signatory'], 'title': entity['title'], 'png': None, 'crop': None,
           'source': None, 'kind': None, 'updated': None, 'updated_by': None}
    if row:
        out.update(signatory=row['signatory'], title=row['title'], updated=row['updated'], updated_by=row['updated_by'])
        if row['signature']:
            out.update(png=bytes(row['signature']) if with_image else b'', source='settings', kind=row['signature_kind'])
    if out['source'] is None:
        png, crop = installed_signature(entity)
        if png:
            out.update(png=png if with_image else b'', crop=crop, source='installed', kind='uploaded')
    return out


def signatories(c) -> dict[str, dict]:
    return {name: profile(c, name, with_image=False) for name in ENTITIES}


def save_profile(c, actor: dict, entity: dict, payload: dict) -> dict:
    current = profile(c, entity['name'], with_image=False)
    signatory = sec_clean(payload.get('signatory', current['signatory']), 'Signatory name', 150, True)
    title = sec_clean(payload.get('title', current['title']), 'Title', 150, True)
    row = c.execute(select(db.company_profiles).where(db.company_profiles.c.entity == entity['name'])).mappings().first()
    signature = bytes(row['signature']) if row and row['signature'] else None
    kind = row['signature_kind'] if row else None
    changed = []
    if payload.get('remove_signature') is True:
        signature, kind = None, None
        changed.append('signature removed')
    elif payload.get('signature'):
        kind = payload.get('signature_kind')
        if kind not in SIGNATURE_KINDS:
            raise ValueError('Choose how the signature was made.')
        signature = clean_signature(decode_image(payload['signature']))
        changed.append('signature')
    if signatory != current['signatory']:
        changed.append('signatory')
    if title != current['title']:
        changed.append('title')
    values = dict(signatory=signatory, title=title, signature=signature, signature_kind=kind, updated=time.time(), updated_by=actor['id'])
    if row:
        c.execute(db.company_profiles.update().where(db.company_profiles.c.entity == entity['name']).values(**values))
    else:
        c.execute(db.company_profiles.insert().values(entity=entity['name'], **values))
    db.log(c, actor, 'company.updated', detail={'entity': entity['name'], 'changed': changed or ['no change'], 'signatory': signatory, 'title': title})
    return profile(c, entity['name'], with_image=False)


def sec_clean(value, label: str, limit: int, required=False) -> str:
    import esign
    return esign.clean(value, label, limit, required)


def public_profile(p: dict, mailbox: str = '') -> dict:
    entity = get_entity(p['entity'])
    return {'entity': p['entity'], 'key': p['key'], 'short': entity['short'], 'signatory': p['signatory'], 'title': p['title'],
            'default_signatory': entity['signatory'], 'default_title': entity['title'], 'has_signature': p['source'] is not None,
            'signature_source': p['source'], 'signature_kind': p['kind'], 'updated': p['updated'], 'color': entity['color'],
            'accent': entity['accent'], 'logo': f"/assets/brand/{entity['logo']}", 'mailbox': mailbox}


# ------------------------------------------------------------------ workspace settings
def workspace(c) -> dict:
    row = c.execute(select(db.settings.c.value).where(db.settings.c.key == 'workspace')).scalar()
    out = dict(WORKSPACE_DEFAULTS)
    if isinstance(row, dict):
        out.update({k: v for k, v in row.items() if k in WORKSPACE_DEFAULTS and isinstance(v, str)})
    return out


def save_workspace(c, actor: dict, payload: dict) -> dict:
    out = workspace(c)
    for key, limit in WORKSPACE_LIMITS.items():
        if key in payload:
            out[key] = sec_clean(payload[key], key.replace('_', ' ').capitalize(), limit, key in ('portal_name', 'copyright_holder', 'help_email'))
    import mailer
    out['help_email'] = mailer.valid_email(out['help_email'])
    if c.execute(select(db.settings.c.key).where(db.settings.c.key == 'workspace')).first():
        c.execute(db.settings.update().where(db.settings.c.key == 'workspace').values(value=out))
    else:
        c.execute(db.settings.insert().values(key='workspace', value=out))
    db.log(c, actor, 'workspace.updated', detail={k: out[k] for k in ('portal_name', 'copyright_holder', 'help_email')})
    _cache.clear()
    return out


_cache: dict = {}


def cached_workspace() -> dict:
    """Workspace settings for places without a connection at hand (email templates); refreshed every minute."""
    if not _cache or _cache['at'] < time.time() - 60:
        try:
            with db.engine.connect() as c:
                _cache.update(value=workspace(c), at=time.time())
        except Exception:  # noqa: BLE001  (the defaults are a safe fallback for an email footer)
            return dict(WORKSPACE_DEFAULTS)
    return dict(_cache['value'])


def public_workspace(c) -> dict:
    return workspace(c) | {'year': time.gmtime().tm_year}


# ------------------------------------------------------------------ email trust (DNS over HTTPS)
DOH = ('https://dns.google/resolve', 'https://cloudflare-dns.com/dns-query')


def txt_records(name: str) -> list[str]:
    """TXT records of a name, or [] when there are none. Raises ValueError when no resolver answers."""
    for url in DOH:
        try:
            r = httpx.get(url, params={'name': name, 'type': 'TXT'}, headers={'accept': 'application/dns-json'}, timeout=6)
            data = r.json()
        except (httpx.HTTPError, ValueError):
            continue
        if data.get('Status') not in (0, 3):     # NOERROR or NXDOMAIN; anything else is a resolver failure
            continue
        out = []
        for answer in data.get('Answer') or []:
            if answer.get('type') == 16:
                parts = re.findall(r'"((?:[^"\\]|\\.)*)"', answer.get('data', ''))
                out.append(''.join(parts) if parts else answer.get('data', ''))
        return out
    raise ValueError('The DNS check could not reach a resolver from this server. Try again later.')


def _tags(record: str) -> dict:
    return {k.strip().lower(): v.strip() for k, _, v in (part.partition('=') for part in record.split(';')) if k.strip()}


def email_trust(domain: str, selector: str, smtp_host: str = '') -> dict:
    domain = domain.strip().lower().rstrip('.')
    if not re.fullmatch(r'(?=.{3,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}', domain):
        raise ValueError('Enter a valid domain, for example scribealign.com.')
    selector = (selector or 'google').strip().lower()
    if not re.fullmatch(r'[a-z0-9._-]{1,63}', selector):
        raise ValueError('Enter a valid DKIM selector.')
    checks = []
    provider = 'google' if 'gmail' in smtp_host or 'google' in smtp_host else 'zoho' if 'zoho' in smtp_host else 'microsoft' if 'office365' in smtp_host or 'outlook' in smtp_host else ''
    spf_include = {'google': 'include:_spf.google.com', 'zoho': 'include:zohomail.com', 'microsoft': 'include:spf.protection.outlook.com'}.get(provider, 'include:<your mail provider>')

    spf = [r for r in txt_records(domain) if r.lower().startswith('v=spf1')]
    if len(spf) == 1:
        checks.append({'key': 'spf', 'name': 'SPF', 'state': 'ok', 'found': spf[0], 'advice': 'SPF is published. Keep a single SPF record.'})
    else:
        checks.append({'key': 'spf', 'name': 'SPF', 'state': 'missing' if not spf else 'warning', 'found': ' | '.join(spf),
                       'advice': 'Publish exactly one SPF record.' if spf else 'Add a TXT record so receivers know which servers may send for the domain.',
                       'record': {'type': 'TXT', 'host': '@', 'value': f'v=spf1 {spf_include} ~all'}})

    dkim = [r for r in txt_records(f'{selector}._domainkey.{domain}') if 'p=' in r]
    checks.append({'key': 'dkim', 'name': f'DKIM (selector "{selector}")', 'state': 'ok' if dkim else 'missing', 'found': dkim[0][:120] + ('…' if dkim and len(dkim[0]) > 120 else '') if dkim else '',
                   'advice': 'Emails are signed with DKIM.' if dkim else ('Turn on DKIM signing in your mail provider (Google Admin › Apps › Gmail › Authenticate email) and publish the record it gives you. '
                                                                         'If your provider uses another selector, enter it above and check again.')})

    dmarc = [r for r in txt_records(f'_dmarc.{domain}') if r.lower().startswith('v=dmarc1')]
    tags = _tags(dmarc[0]) if dmarc else {}
    policy, pct = tags.get('p', '').lower(), tags.get('pct', '100')
    enforced = policy in ('quarantine', 'reject') and pct == '100'
    checks.append({'key': 'dmarc', 'name': 'DMARC', 'state': 'ok' if enforced else 'warning' if dmarc else 'missing', 'found': dmarc[0] if dmarc else '',
                   'advice': 'DMARC is enforced, as the verified logo and tick require.' if enforced else
                   ('DMARC is published but not enforced. The verified logo and tick need p=quarantine or p=reject (100 %). Move to it once reports show your mail passes.' if dmarc
                    else 'Add a DMARC record. Start with p=none to watch reports, then move to quarantine.'),
                   'record': None if enforced else {'type': 'TXT', 'host': '_dmarc', 'value': f'v=DMARC1; p=quarantine; pct=100; rua=mailto:dmarc@{domain}; adkim=s; aspf=s'}})

    bimi = [r for r in txt_records(f'default._bimi.{domain}') if r.lower().startswith('v=bimi1')]
    btags = _tags(bimi[0]) if bimi else {}
    has_cert = bool(btags.get('a'))
    checks.append({'key': 'bimi', 'name': 'BIMI (logo and verified tick)', 'state': 'ok' if bimi and has_cert else 'warning' if bimi else 'missing', 'found': bimi[0] if bimi else '',
                   'advice': ('BIMI is published with a certificate. Gmail shows the logo, and the blue tick with a Verified Mark Certificate.' if bimi and has_cert else
                              'BIMI is published without a certificate. Gmail needs a Verified Mark Certificate (VMC) or Common Mark Certificate (CMC) to show the logo.' if bimi else
                              'To show the company logo and the verified tick in Gmail, publish BIMI with a logo in SVG Tiny PS format and a Verified Mark Certificate.'),
                   'record': None if bimi and has_cert else {'type': 'TXT', 'host': 'default._bimi', 'value': f'v=BIMI1; l=https://{domain}/bimi/logo.svg; a=https://{domain}/bimi/vmc.pem'}})
    return {'domain': domain, 'selector': selector, 'provider': provider, 'checks': checks, 'checked': time.time()}


# ------------------------------------------------------------------ routes
def install(app) -> None:  # noqa: C901  (route table)
    def super_admin(request, c):
        a = sec.get_actor(request, c)
        if a['role'] != 'super_admin':
            raise HTTPException(403, 'Only the Super Admin can change company settings.')
        return a

    @app.get('/api/public/workspace')
    def api_public_workspace():
        with db.engine.connect() as c:
            return public_workspace(c)

    @app.get('/api/admin/companies')
    def api_companies(request: Request):
        import mailer
        with db.engine.connect() as c:
            super_admin(request, c)
            return {'companies': [public_profile(profile(c, name, with_image=False), mailer.load(c, name)['address']) for name in ENTITIES],
                    'workspace': workspace(c)}

    @app.post('/api/admin/companies/clean-signature')
    def api_clean_signature(request: Request, payload: dict = Body(...)):
        with db.engine.connect() as c:
            super_admin(request, c)
        png = clean_signature(decode_image(payload.get('image')))
        return {'image': 'data:image/png;base64,' + base64.b64encode(png).decode()}

    @app.post('/api/admin/companies/{key}')
    def api_save_company(key: str, request: Request, payload: dict = Body(...)):
        import mailer
        with db.engine.begin() as c:
            a = super_admin(request, c)
            entity = entity_by_key(key)
            p = save_profile(c, a, entity, payload)
            return {'company': public_profile(p, mailer.load(c, entity['name'])['address'])}

    @app.get('/api/companies/{key}/signature.png')
    def api_signature_image(key: str, request: Request):
        with db.engine.connect() as c:
            a = sec.get_actor(request, c)
            entity = entity_by_key(key)
            sec.ensure_company(a, entity['name'])
            p = profile(c, entity['name'])
        if not p['png']:
            raise HTTPException(404, 'No signature is saved for this company.')
        return Response(p['png'], media_type='image/png' if p['png'].startswith(b'\x89PNG') else 'image/jpeg', headers={'Cache-Control': 'no-store'})

    @app.post('/api/admin/workspace')
    def api_save_workspace(request: Request, payload: dict = Body(...)):
        with db.engine.begin() as c:
            a = super_admin(request, c)
            return {'workspace': save_workspace(c, a, payload)}

    @app.post('/api/admin/email-trust')
    def api_email_trust(request: Request, payload: dict = Body(...)):
        import mailer
        with db.engine.begin() as c:
            a = super_admin(request, c)
            entity = entity_by_key(str(payload.get('company', '')))
            box = mailer.load(c, entity['name'])
            domain = payload.get('domain') or (box['address'].split('@')[-1] if box['address'] else '')
            db.log(c, a, 'email_trust.checked', detail={'entity': entity['name'], 'domain': domain})
        result = email_trust(str(domain), str(payload.get('selector') or ''), box.get('smtp_host', ''))
        return result | {'company': entity['key'], 'mailbox': box['address'], 'display_name': box['display_name']}

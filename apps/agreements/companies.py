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
import os
import re
import time
import xml.etree.ElementTree as ET

import httpx
from fastapi import Body, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select

import database as db
import security as sec
from entities import ENTITIES, get_entity, signature_path

SIGNATURE_KINDS = ('uploaded', 'drawn', 'typed')
SCALE_RANGE = (0.3, 1.0)      # signature size as a share of the largest box on the signature line (2.3 x 0.96 in)
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
           'source': None, 'kind': None, 'updated': None, 'updated_by': None, 'scale': entity['signature_scale']}
    if row:
        out.update(signatory=row['signatory'], title=row['title'], updated=row['updated'], updated_by=row['updated_by'],
                   scale=row['signature_scale'] or entity['signature_scale'])
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
    scale = row['signature_scale'] if row else None
    if payload.get('signature_scale') is not None:
        try:
            if isinstance(payload['signature_scale'], bool):
                raise ValueError
            wanted = round(float(payload['signature_scale']), 2)
        except (TypeError, ValueError):
            raise ValueError('Choose a signature size.') from None
        if not SCALE_RANGE[0] <= wanted <= SCALE_RANGE[1]:
            raise ValueError('Choose a signature size between 30 % and 100 %.')
        if wanted != current['scale']:
            changed.append(f'signature size {round(wanted * 100)} %')
        scale = None if wanted == entity['signature_scale'] else wanted      # the default stays empty, so it follows the default
    if signatory != current['signatory']:
        changed.append('signatory')
    if title != current['title']:
        changed.append('title')
    values = dict(signatory=signatory, title=title, signature=signature, signature_kind=kind, signature_scale=scale, updated=time.time(), updated_by=actor['id'])
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
            'signature_source': p['source'], 'signature_kind': p['kind'], 'signature_scale': p['scale'], 'default_signature_scale': entity['signature_scale'],
            'updated': p['updated'], 'color': entity['color'],
            'accent': entity['accent'], 'logo': entity['logo_url'], 'custom': entity.get('custom', False), 'mailbox': mailbox}


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


# ------------------------------------------------------------------ verified sender: BIMI logo and mark certificate
# Gmail shows a company's logo next to its emails once the domain enforces DMARC and publishes a BIMI record pointing at
# an SVG Tiny PS logo; the blue verified tick additionally needs a Verified Mark Certificate (VMC, which requires a
# registered trademark). A Common Mark Certificate (CMC) shows the logo without the tick. The portal stores both files
# and serves them over HTTPS at /bimi/<company>.svg and /bimi/<company>.pem, so only one DNS record is needed.
BIMI_SVG_LIMIT, BIMI_PEM_LIMIT = 32_768, 65_536
BIMI_EKU = '1.3.6.1.5.5.7.3.31'              # id-kp-BrandIndicatorforMessageIdentification
MARK_TYPE_OID = '1.3.6.1.4.1.53087.1.13'      # mark type in the certificate subject
VMC_MARKS = ('Registered Mark', 'Government Mark')
CMC_MARKS = ('Prior Use Mark', 'Modified Registered Mark')
SVG_FORBIDDEN = ('script', 'foreignObject', 'image', 'animate', 'animateMotion', 'animateTransform', 'animateColor', 'set', 'iframe', 'video', 'audio')


def _local(tag: str) -> str:
    return str(tag).rsplit('}', 1)[-1]


def bimi_svg_check(text) -> dict:
    """Validate a BIMI logo against the SVG Tiny Portable/Secure rules mailbox providers enforce."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Choose the SVG logo file.')
    raw = text.encode('utf-8')
    if len(raw) > BIMI_SVG_LIMIT:
        raise ValueError('The BIMI logo must be 32 KB or smaller.')
    if re.search(r'<!\s*(DOCTYPE|ENTITY)', text, re.I):
        raise ValueError('The logo must not contain a DOCTYPE or entities. Export it again as SVG Tiny PS.')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ValueError('That file is not a valid SVG image.') from None
    if _local(root.tag) != 'svg':
        raise ValueError('That file is not an SVG image.')
    problems, warnings = [], []
    if root.get('version') != '1.2':
        problems.append('the <svg> element needs version="1.2"')
    if root.get('baseProfile') != 'tiny-ps':
        problems.append('the <svg> element needs baseProfile="tiny-ps"')
    if root.get('x') is not None or root.get('y') is not None:
        problems.append('the <svg> element must not have x or y attributes')
    if not any(_local(child.tag) == 'title' for child in root):
        problems.append('a <title> with the company name is required')
    for node in root.iter():
        name = _local(node.tag)
        if name in SVG_FORBIDDEN:
            problems.append(f'<{name}> elements are not allowed')
        for attr, value in node.attrib.items():
            if _local(attr).lower().startswith('on'):
                problems.append('event handlers are not allowed')
            if _local(attr) == 'href' and not str(value).startswith('#'):
                problems.append('links to other files are not allowed')
    box = (root.get('viewBox') or '').replace(',', ' ').split()
    try:
        if len(box) != 4 or abs(float(box[2]) - float(box[3])) > 0.5:
            warnings.append('a square logo (equal viewBox width and height) is recommended')
    except ValueError:
        warnings.append('the viewBox could not be read')
    if problems:
        raise ValueError('The logo is not SVG Tiny PS yet: ' + '; '.join(sorted(set(problems))) + '. Ask your designer to export it as SVG Tiny PS.')
    return {'size': len(raw), 'warnings': warnings}


def bimi_cert_info(pem) -> dict:
    """What kind of mark certificate this is (VMC gives the tick, CMC the logo only), who issued it and until when."""
    if not isinstance(pem, str) or '-----BEGIN CERTIFICATE-----' not in pem:
        raise ValueError('Choose the certificate file (.pem) your certificate authority issued.')
    if len(pem.encode()) > BIMI_PEM_LIMIT:
        raise ValueError('The certificate file must be 64 KB or smaller.')
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
    except ImportError:          # the details are informational; the file is still served as uploaded
        return {'kind': 'unknown', 'note': 'Saved. The certificate details could not be read on this server.'}
    try:
        certs = x509.load_pem_x509_certificates(pem.encode())
    except Exception:  # noqa: BLE001
        raise ValueError('The certificate could not be read. Upload the PEM file with the full chain from your certificate authority.') from None
    leaf = certs[0]
    try:
        eku = [o.dotted_string for o in leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value]
    except x509.ExtensionNotFound:
        eku = []
    try:
        names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = []
    mark = next((str(a.value) for a in leaf.subject if a.oid.dotted_string == MARK_TYPE_OID), '')
    org = next((str(a.value) for a in leaf.subject if a.oid == NameOID.ORGANIZATION_NAME), '')
    issuer = next((str(a.value) for a in leaf.issuer if a.oid == NameOID.ORGANIZATION_NAME), '') or leaf.issuer.rfc4514_string()
    if BIMI_EKU not in eku and not mark:
        raise ValueError('This is not a BIMI mark certificate. Upload the Verified Mark Certificate (VMC) or Common Mark Certificate (CMC).')
    until = leaf.not_valid_after_utc.timestamp()
    kind = 'VMC' if mark in VMC_MARKS else 'CMC' if mark in CMC_MARKS else 'BIMI'
    return {'kind': kind, 'mark_type': mark, 'organization': org, 'issuer': issuer, 'domains': list(names), 'valid_until': until,
            'expired': until < time.time(), 'chain': len(certs)}


def bimi_load(c, key: str) -> dict:
    value = c.execute(select(db.settings.c.value).where(db.settings.c.key == f'bimi:{key}')).scalar()
    return value if isinstance(value, dict) else {}


def bimi_save(c, key: str, value: dict) -> None:
    if c.execute(select(db.settings.c.key).where(db.settings.c.key == f'bimi:{key}')).first():
        c.execute(db.settings.update().where(db.settings.c.key == f'bimi:{key}').values(value=value))
    else:
        c.execute(db.settings.insert().values(key=f'bimi:{key}', value=value))


def portal_origin(request: Request | None = None) -> str:
    origin = os.getenv('TFSC_ORIGIN', '').rstrip('/')
    return origin or (str(request.base_url).rstrip('/') if request is not None else '')


def bimi_status(c, entity: dict, origin: str) -> dict:
    data = bimi_load(c, entity['key'])
    logo_url, cert_url = f"{origin}/bimi/{entity['key']}.svg", f"{origin}/bimi/{entity['key']}.pem"
    has_logo, has_cert = bool(data.get('svg')), bool(data.get('pem'))
    value = f'v=BIMI1; l={logo_url}; a={cert_url}' if has_cert else f'v=BIMI1; l={logo_url};'
    return {'company': entity['key'], 'logo_url': logo_url, 'cert_url': cert_url, 'has_logo': has_logo, 'has_cert': has_cert,
            'logo': data.get('logo'), 'cert': data.get('cert'), 'https': origin.startswith('https://'),
            'record': {'type': 'TXT', 'host': 'default._bimi', 'value': value}, 'updated': data.get('updated')}


def bimi_update(c, actor: dict, entity: dict, payload: dict) -> None:
    data = bimi_load(c, entity['key'])
    changed = []
    if payload.get('remove_logo') is True:
        data.pop('svg', None); data.pop('logo', None); changed.append('logo removed')
    elif payload.get('svg') is not None:
        data['logo'] = bimi_svg_check(payload['svg']); data['svg'] = payload['svg']; changed.append('logo')
    if payload.get('remove_cert') is True:
        data.pop('pem', None); data.pop('cert', None); changed.append('certificate removed')
    elif payload.get('pem') is not None:
        data['cert'] = bimi_cert_info(payload['pem']); data['pem'] = payload['pem']; changed.append('certificate')
    if not changed:
        raise ValueError('Choose a logo or a certificate to upload.')
    data['updated'] = time.time()
    bimi_save(c, entity['key'], data)
    db.log(c, actor, 'email_trust.bimi_updated', detail={'entity': entity['name'], 'changed': changed})


def verified_tick(checks: list, status: dict) -> dict:
    """What still stands between this company's emails and Gmail's logo + blue verified tick."""
    state = {x['key']: x for x in checks}
    blockers = []
    if state.get('spf', {}).get('state') != 'ok':
        blockers.append('Publish a single SPF record.')
    if state.get('dkim', {}).get('state') != 'ok':
        blockers.append('Turn on DKIM signing at the mail provider.')
    if state.get('dmarc', {}).get('state') != 'ok':
        blockers.append('Move DMARC to p=quarantine or p=reject at 100 %.')
    if not status['https']:
        blockers.append('The portal must be reached over HTTPS so Gmail can fetch the logo and certificate.')
    if not status['has_logo']:
        blockers.append('Upload the logo as SVG Tiny PS.')
    cert = status.get('cert') or {}
    if not status['has_cert']:
        blockers.append('Upload the Verified Mark Certificate (VMC). It needs a registered trademark of the logo.')
    elif cert.get('expired'):
        blockers.append('The mark certificate has expired. Renew it and upload the new file.')
    elif cert.get('kind') == 'CMC':
        blockers.append('This is a Common Mark Certificate: Gmail shows the logo but not the blue tick. The tick needs a VMC.')
    published = state.get('bimi', {}).get('found', '')
    if not published:
        blockers.append('Publish the BIMI record shown below at default._bimi.')
    elif status['logo_url'] not in published or (status['has_cert'] and status['cert_url'] not in published):
        blockers.append('The published BIMI record points elsewhere. Replace it with the record shown below.')
    return {'ready': not blockers, 'blockers': blockers}


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

    @app.get('/api/admin/bimi/{key}')
    def api_bimi(key: str, request: Request):
        with db.engine.connect() as c:
            super_admin(request, c)
            return bimi_status(c, entity_by_key(key), portal_origin(request))

    @app.post('/api/admin/bimi/{key}')
    def api_bimi_update(key: str, request: Request, payload: dict = Body(...)):
        with db.engine.begin() as c:
            a = super_admin(request, c)
            entity = entity_by_key(key)
            bimi_update(c, a, entity, payload)
            return bimi_status(c, entity, portal_origin(request))

    @app.get('/bimi/{name}')
    def bimi_file(name: str):
        """Public on purpose: mailbox providers fetch the logo and certificate from here."""
        key, _, ext = name.rpartition('.')
        if ext not in ('svg', 'pem') or not re.fullmatch(r'[a-z0-9_-]{1,40}', key):
            raise HTTPException(404, 'Not found.')
        with db.engine.connect() as c:
            data = bimi_load(c, key)
        body = data.get('svg' if ext == 'svg' else 'pem')
        if not body:
            raise HTTPException(404, 'Not found.')
        kind = 'image/svg+xml' if ext == 'svg' else 'application/pem-certificate-chain'
        return Response(body, media_type=kind, headers={'Cache-Control': 'public, max-age=3600', 'X-Content-Type-Options': 'nosniff',
                                                        'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'"})

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
        with db.engine.connect() as c:
            status = bimi_status(c, entity, portal_origin(request))
        return result | {'company': entity['key'], 'mailbox': box['address'], 'display_name': box['display_name'], 'bimi': status,
                         'verified_tick': verified_tick(result['checks'], status)}

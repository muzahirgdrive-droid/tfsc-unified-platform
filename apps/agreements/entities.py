"""Company identity: the three built-in companies, plus any a Super Admin added from the app.

The built-ins stay defined here (their letterhead, signature and logo files live in assets/). Companies added
from the app are stored in the `companies` table and merged in at read time. ``ENTITIES`` behaves like the old
module-level dict (iteration, ``name in ENTITIES``, ``ENTITIES[name]``, ``list(ENTITIES)``) but reflects the
database. Built-ins always come first, so existing positions — used to index company mailboxes — never shift.
"""
from __future__ import annotations

import os
import time
from collections.abc import Mapping
from pathlib import Path

APP = Path(__file__).resolve().parent
DATA = Path(os.getenv('TFSC_DATA_DIR', str(APP / '.data'))).resolve()

_BUILTINS = {
 'SCRIBE ALIGN LLC':dict(key='scribe',name='SCRIBE ALIGN LLC',short='Scribe Align',display='Scribe Align LLC',dark='#000F18',signatory='Naeem Abbas',title='Chief Executive Officer',signature_scale=0.6,governing_state='Nevada',timezone='America/Los_Angeles',logo='scribe.png',color='#004B79',accent='#00AEEF',signature='Naeem Abbas.png',template='letterhead.docx',sha256='cef540f5dda4700eae10ebaf86d8707b67b814c90ea5ad33264142cc34e2e895',address='9436 W. Lake Mead Blvd Ste 5 PMB 1034, Las Vegas, NV 89134',email='',width=10368,top=1872,bottom=1512,left=936,right=936),
 'EXTENDED HEALTH SERVICES INC.':dict(key='ehs',name='EXTENDED HEALTH SERVICES INC.',short='Extended Health Services',display='Extended Health Services Inc.',dark='#101A24',signatory='Naeem Abbas',title='Chief Executive Officer',signature_scale=0.6,governing_state='Missouri',timezone='America/Chicago',logo='ehs.png',color='#1C2A39',accent='#FBCD35',signature='Naeem Abbas.png',template='letterhead-ehs.docx',sha256='2adb13f35edd41a18c850d6fb7cb0500cb2e60b6376a4ee46060104955d06b5b',address='105 Russel St, Hayti, MO 63851',email='info@extendedhealthservices.us',width=9590,top=1900,bottom=1800,left=1158,right=1158),
 '24-7 CONSULTANCY LLC':dict(key='24-7',name='24-7 CONSULTANCY LLC',short='24-7 Consultancy',display='24-7 Consultancy LLC',dark='#0F2747',signatory='Danish Ather',title='Chief Operating Officer',signature_scale=0.85,governing_state='Georgia',timezone='America/New_York',logo='24-7.png',color='#1D4277',accent='#189BD2',signature='Danish Ather.png',template='letterhead-24-7.docx',sha256='697dcf72bda5dafeb393b1bc86e01ad14add1b866acd2a316e584bb1b6c19cc4',address='1645 Metropolitan Blvd, Tallahassee, FL 32308',email='info@24-7consultancyllc.com',width=10368,top=2050,bottom=1800,left=936,right=936),
}
DEFAULT_ENTITY = 'SCRIBE ALIGN LLC'

# The letterhead, logo and page geometry a company's agreements use; set on every entity (built-in or custom).
for _e in _BUILTINS.values():
    _e['custom'] = False
    _e['template_path'] = APP / 'assets' / _e['template']
    _e['logo_path'] = APP / 'assets' / 'brand' / _e['logo']
    _e['logo_url'] = f"/assets/brand/{_e['logo']}"
    _e.setdefault('services', None)      # built-ins use the standard global service catalogue
    _e.setdefault('doc_types', ['baa', 'services', 'nda'])

_LETTERHEADS = DATA / 'letterheads'
_LOGOS = DATA / 'company-logos'
_CACHE: dict = {'at': 0.0, 'ents': None}
_TTL = 3.0


def _materialize(folder: Path, name: str, content: bytes) -> Path:
    """Write bytes from the database to a cache file once (content-addressed), for the document engine to read."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if not path.is_file() or path.stat().st_size != len(content):
        tmp = path.with_name(path.name + '.writing')
        tmp.write_bytes(content)
        os.replace(tmp, path)
    return path


def _build_custom(row: dict) -> dict:
    key = row['key']
    sha = row['letterhead_sha256']
    template_path = _materialize(_LETTERHEADS, f'{key}-{sha[:16]}.docx', bytes(row['letterhead']))
    logo_url = f"/api/companies/{key}/logo.png"
    logo_path = None
    if row.get('logo'):
        ext = (row.get('logo_ext') or 'png').lstrip('.')
        logo_path = _materialize(_LOGOS, f'{key}.{ext}', bytes(row['logo']))
    return dict(key=key, name=row['name'], short=row['short'], display=row['display'], dark=row['dark'],
                signatory=row['signatory'], title=row['title'], signature_scale=row['signature_scale'],
                governing_state=row['governing_state'], timezone=row['timezone'], color=row['color'], accent=row['accent'],
                address=row['address'], email=row['email'] or '', width=row['width'], top=row['top'], bottom=row['bottom'],
                left=row['left'], right=row['right'], services=row['services'], doc_types=row['doc_types'],
                signature=f'{key}-installed.png',  # no installed file; the signature comes from company_profiles (database)
                template='', template_path=template_path, sha256=sha, logo=f'{key}.png', logo_path=logo_path, logo_url=logo_url,
                custom=True)


def _load(force: bool = False) -> dict:
    if not force and _CACHE['ents'] is not None and time.time() - _CACHE['at'] < _TTL:
        return _CACHE['ents']
    ents = dict(_BUILTINS)
    try:
        import database as db
        from sqlalchemy import select
        with db.engine.connect() as c:
            rows = c.execute(select(db.companies).where(db.companies.c.active.is_(True)).order_by(db.companies.c.created, db.companies.c.key)).mappings().all()
        for row in rows:
            try:
                ents[row['name']] = _build_custom(dict(row))
            except Exception:  # noqa: BLE001  (one unreadable company must not hide the others)
                continue
    except Exception:  # noqa: BLE001  (table missing during bootstrap, or the database is briefly unavailable)
        pass
    _CACHE['ents'] = ents
    _CACHE['at'] = time.time()
    return ents


def invalidate() -> None:
    """Force the next lookup to re-read the companies table (call after a company is added or changed)."""
    _CACHE['at'] = 0.0


class _Registry(Mapping):
    def __getitem__(self, key):
        return _load()[key]

    def __iter__(self):
        return iter(_load())

    def __len__(self):
        return len(_load())


ENTITIES = _Registry()


def get_entity(name=None):
    if name is None or name == '':
        name = DEFAULT_ENTITY
    ents = _load()
    if name not in ents:
        ents = _load(force=True)      # a company added moments ago may not be in the short-lived cache yet
    if name not in ents:
        raise ValueError('Select a supported contracting company.')
    return ents[name]


def signature_path(data_dir, entity):
    return Path(data_dir) / 'signatures' / get_entity(entity)['signature']

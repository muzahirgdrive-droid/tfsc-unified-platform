"""Company identity: the three built-in companies, plus any a Super Admin added from the app.

The built-ins stay defined here (their letterhead, signature and logo files live in assets/). Companies added
from the app are stored in the `companies` table and merged in at read time. ``ENTITIES`` behaves like the old
module-level dict (iteration, ``name in ENTITIES``, ``ENTITIES[name]``, ``list(ENTITIES)``) but reflects the
database. Built-ins always come first, so existing positions — used to index company mailboxes — never shift.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Mapping
from pathlib import Path

APP = Path(__file__).resolve().parent
DATA = Path(os.getenv('TFSC_DATA_DIR', str(APP / '.data'))).resolve()

_BUILTINS = {
 'SCRIBE ALIGN LLC':dict(key='scribe',name='SCRIBE ALIGN LLC',short='Scribe Align',display='Scribe Align LLC',dark='#000F18',signatory='Naeem Abbas',title='Chief Executive Officer',signature_scale=0.6,governing_state='Nevada',timezone='America/Los_Angeles',logo='scribe.png',color='#004B79',accent='#00AEEF',signature='Naeem Abbas.png',template='letterhead.docx',sha256='cef540f5dda4700eae10ebaf86d8707b67b814c90ea5ad33264142cc34e2e895',address='9436 W. Lake Mead Blvd Ste 5 PMB 1034, Las Vegas, NV 89134',email='',width=10368,top=1872,bottom=1512,left=936,right=936),
 'EXTENDED HEALTH SERVICES INC.':dict(key='ehs',name='EXTENDED HEALTH SERVICES INC.',short='Extended Health Services',display='Extended Health Services Inc.',dark='#101A24',signatory='Naeem Abbas',title='Chief Executive Officer',signature_scale=0.6,governing_state='Missouri',timezone='America/Chicago',logo='ehs.png',color='#1C2A39',accent='#FBCD35',signature='Naeem Abbas.png',template='letterhead-ehs.docx',sha256='2adb13f35edd41a18c850d6fb7cb0500cb2e60b6376a4ee46060104955d06b5b',address='105 Russel St, Hayti, MO 63851',email='info@extendedhealthservices.us',width=10034,top=1900,bottom=1800,left=936,right=936),
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
# How long one process trusts its copy. Short, because another process may have changed a company; cheap, because the
# refresh reads only the small columns (letterheads and logos are read from the database only when their cache file is
# missing) on a connection of its own.
_TTL = float(os.getenv('TFSC_COMPANY_CACHE_SECONDS', '5'))
_LOCK = threading.Lock()
_SIDE: dict = {'for': None, 'engine': None}
log = logging.getLogger('tfsc.entities')


def _registry_engine(db):
    """The connection the registry is read with.

    The registry is looked up inside requests that already hold a pooled connection. Taking a second connection from
    the same pool there is what froze the site under load (every thread held one connection and waited for another).
    On PostgreSQL the registry therefore has a tiny pool of its own; one refresh runs at a time, so it never needs more.
    """
    main = db.engine
    if main.dialect.name != 'postgresql':
        return main
    if _SIDE['for'] is not main:
        from sqlalchemy import create_engine
        _SIDE['engine'] = create_engine(main.url, pool_size=1, max_overflow=1, pool_timeout=5, pool_pre_ping=True, pool_recycle=1800)
        _SIDE['for'] = main
    return _SIDE['engine']


def _materialize(folder: Path, name: str, content: bytes) -> Path:
    """Write bytes from the database to a cache file once (content-addressed), for the document engine to read."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if not path.is_file() or path.stat().st_size != len(content):
        tmp = path.with_name(path.name + '.writing')
        tmp.write_bytes(content)
        os.replace(tmp, path)
    return path


def _build_custom(row: dict, c=None, db=None) -> dict:
    key = row['key']
    sha = row['letterhead_sha256']
    template_path = _LETTERHEADS / f'{key}-{sha[:16]}.docx'
    if 'letterhead' in row:
        template_path = _materialize(_LETTERHEADS, template_path.name, bytes(row['letterhead']))
    elif not template_path.is_file():        # first use in this container: fetch the file once
        from sqlalchemy import select
        body = c.execute(select(db.companies.c.letterhead).where(db.companies.c.key == key)).scalar()
        template_path = _materialize(_LETTERHEADS, template_path.name, bytes(body))
    logo_url = f"/api/companies/{key}/logo.png"
    logo_path = None
    if row.get('logo'):
        ext = (row.get('logo_ext') or 'png').lstrip('.')
        logo_path = _materialize(_LOGOS, f'{key}.{ext}', bytes(row['logo']))
    elif row.get('logo_size'):
        ext = (row.get('logo_ext') or 'png').lstrip('.')
        logo_path = _LOGOS / f'{key}.{ext}'
        if not logo_path.is_file() or logo_path.stat().st_size != row['logo_size']:
            from sqlalchemy import select
            body = c.execute(select(db.companies.c.logo).where(db.companies.c.key == key)).scalar()
            logo_path = _materialize(_LOGOS, logo_path.name, bytes(body)) if body else None
    return dict(key=key, name=row['name'], short=row['short'], display=row['display'], dark=row['dark'],
                signatory=row['signatory'], title=row['title'], signature_scale=row['signature_scale'],
                governing_state=row['governing_state'], timezone=row['timezone'], color=row['color'], accent=row['accent'],
                address=row['address'], email=row['email'] or '', width=row['width'], top=row['top'], bottom=row['bottom'],
                left=row['left'], right=row['right'], services=row['services'], doc_types=row['doc_types'],
                signature=f'{key}-installed.png',  # no installed file; the signature comes from company_profiles (database)
                template='', template_path=template_path, sha256=sha, logo=f'{key}.png', logo_path=logo_path, logo_url=logo_url,
                custom=True)


# Details a Super Admin changed for a built-in company (Admin > Company settings > Edit company details). They live in the
# settings table: "company:<key>" holds the small fields, "company:<key>:logo" and "company:<key>:letterhead" the files.
# The legal name never changes: it identifies the company on every agreement, envelope, mailbox and permission.
BUILTIN_EDITABLE = ('short', 'display', 'address', 'email', 'governing_state', 'timezone', 'color', 'accent', 'dark')
GEOMETRY = ('width', 'top', 'bottom', 'left', 'right')


def override_key(key: str, part: str = '') -> str:
    return f'company:{key}' + (f':{part}' if part else '')


def _override_file(c, db, key: str, part: str, folder: Path, name: str) -> Path | None:
    """The uploaded file for a built-in, written once to the cache folder (read from the database only when missing)."""
    path = folder / name
    if path.is_file():
        return path
    import base64
    from sqlalchemy import select
    value = c.execute(select(db.settings.c.value).where(db.settings.c.key == override_key(key, part))).scalar()
    if not value or not value.get('data'):
        return None
    return _materialize(folder, name, base64.b64decode(value['data']))


def _apply_override(base: dict, o: dict, c, db) -> dict:
    e = dict(base)
    for field in BUILTIN_EDITABLE:
        if field in o and (o[field] or field == 'email'):
            e[field] = o[field]
    key = e['key']
    if o.get('logo_sha'):
        path = _override_file(c, db, key, 'logo', _LOGOS, f"{key}-{o['logo_sha'][:16]}.png")
        if path:
            e.update(logo_path=path, logo_url=f"/api/companies/{key}/logo.png?v={o['logo_sha'][:12]}", logo=path.name)
    if o.get('letterhead_sha') and all(isinstance(o.get(g), int) for g in GEOMETRY):
        path = _override_file(c, db, key, 'letterhead', _LETTERHEADS, f"{key}-{o['letterhead_sha'][:16]}.docx")
        if path:
            e.update(template_path=path, sha256=o['letterhead_sha'], **{g: o[g] for g in GEOMETRY})
    elif all(isinstance(o.get(g), int) for g in GEOMETRY):
        e.update({g: o[g] for g in GEOMETRY})          # new margins on the original letterhead
    e['edited'] = True
    return e


def _read() -> dict:
    """Read the registry from the database. Raises when the database cannot be read (the caller keeps its last copy)."""
    import database as db
    from sqlalchemy import func, select
    ents = dict(_BUILTINS)
    with _registry_engine(db).connect() as c:
        by_key = {e['key']: name for name, e in _BUILTINS.items()}
        small = select(db.settings.c.key, db.settings.c.value).where(db.settings.c.key.like('company:%'), ~db.settings.c.key.like('company:%:%'))
        for setting, value in c.execute(small).all():
            key = setting.split(':', 1)[1]
            if ':' in key or key not in by_key or not isinstance(value, dict):
                continue
            try:
                ents[by_key[key]] = _apply_override(_BUILTINS[by_key[key]], value, c, db)
            except Exception:  # noqa: BLE001  (an unreadable change never hides the company: it keeps its original details)
                log.warning('company details of %s could not be applied', key, exc_info=True)
        columns = [col for col in db.companies.c if col.name not in ('letterhead', 'logo')]
        rows = c.execute(select(*columns, func.length(db.companies.c.logo).label('logo_size')).where(db.companies.c.active.is_(True))
                         .order_by(db.companies.c.created, db.companies.c.key)).mappings().all()
        for row in rows:
            try:
                ents[row['name']] = _build_custom(dict(row), c, db)
            except Exception:  # noqa: BLE001  (one unreadable company must not hide the others)
                log.warning('company %s could not be loaded', row['key'], exc_info=True)
    return ents


def _load(force: bool = False) -> dict:
    ents = _CACHE['ents']
    if not force and ents is not None and time.time() - _CACHE['at'] < _TTL:
        return ents
    # One refresh at a time. While one runs, everybody else keeps using the copy they have.
    if ents is not None and not force:
        if not _LOCK.acquire(blocking=False):
            return ents
    else:
        _LOCK.acquire()
    try:
        if not force and _CACHE['ents'] is not None and time.time() - _CACHE['at'] < _TTL:
            return _CACHE['ents']
        try:
            fresh = _read()
        except Exception:  # noqa: BLE001  (tables missing during the very first start, or the database is briefly unavailable)
            # A failed read is never remembered: the last good copy stays in use and the next lookup tries again.
            if _CACHE['ents'] is not None:
                log.warning('company registry could not be refreshed; using the last copy', exc_info=True)
                _CACHE['at'] = time.time() - _TTL + 2     # try again in a couple of seconds, not on every lookup
                return _CACHE['ents']
            return dict(_BUILTINS)
        _CACHE['ents'] = fresh
        _CACHE['at'] = time.time()
        return fresh
    finally:
        _LOCK.release()


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

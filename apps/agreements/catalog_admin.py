"""Admin > Services & pricing: the services agreements can include and the pricing models each one may use.

The standard catalogue (contract_content / revised_content) stays in the code and is never rewritten. What a Super Admin
changes is kept in the settings table under ``catalog`` and laid over it at read time:

    {"services": {"<id>": {name, description, category, models, active, scope, responsibilities, exclusions}},
     "added":    {"<svc_id>": {... a service added from the app ...}},
     "models":   {"<pm_id>": {label, billing, fields: [{key, label, kind, required, choices}], wording}}}

Agreements already generated keep exactly what they were generated with: each generated service stores its own copy of
its name, scope and fee wording in the agreement's payload. A change here only affects agreements created afterwards.
"""
from __future__ import annotations

import re
import threading
import time

from fastapi import Body, HTTPException, Request
from sqlalchemy import select

import database as db
import security as sec

KEY = 'catalog'
FIELD_KINDS = ('amount', 'percent', 'number', 'date', 'text', 'choice')
_CACHE: dict = {'at': 0.0, 'value': None}
_LOCK = threading.Lock()
TTL = 5.0


def _base():
    from revised_content import CATALOG
    return CATALOG


def builtin_models() -> dict:
    """The pricing models the code knows how to word (id -> label)."""
    labels = {}
    for spec in _base():
        for m in spec['models']:
            labels.setdefault(m['id'], m['label'])
    return labels


def _empty() -> dict:
    return {'services': {}, 'added': {}, 'models': {}}


def read(c=None) -> dict:
    """The stored changes (cached for a few seconds per process; read on its own small connection)."""
    now = time.time()
    if c is None and _CACHE['value'] is not None and now - _CACHE['at'] < TTL:
        return _CACHE['value']
    def load(conn):
        value = conn.execute(select(db.settings.c.value).where(db.settings.c.key == KEY)).scalar()
        out = _empty()
        if isinstance(value, dict):
            for k in out:
                if isinstance(value.get(k), dict):
                    out[k] = value[k]
        return out
    if c is not None:
        return load(c)
    with _LOCK:
        if _CACHE['value'] is not None and time.time() - _CACHE['at'] < TTL:
            return _CACHE['value']
        try:
            import entities
            with entities._registry_engine(db).connect() as conn:
                value = load(conn)
        except Exception:  # noqa: BLE001  (first start before the tables exist, or a short outage: the standard catalogue)
            return _CACHE['value'] or _empty()
        _CACHE.update(at=time.time(), value=value)
        return value


def invalidate() -> None:
    _CACHE['at'] = 0.0


def custom_models(stored: dict | None = None) -> dict:
    stored = stored if stored is not None else read()
    return {k: v for k, v in stored['models'].items() if isinstance(v, dict) and v.get('label')}


def model_labels(stored: dict | None = None) -> dict:
    return builtin_models() | {k: v['label'] for k, v in custom_models(stored).items()}


def catalog(stored: dict | None = None, include_inactive: bool = False) -> list[dict]:
    """The effective service catalogue: the standard services with their changes, then the services added in the app."""
    stored = stored if stored is not None else read()
    labels = model_labels(stored)
    out = []
    for spec in _base():
        o = stored['services'].get(spec['id']) or {}
        s = dict(spec)
        for key in ('name', 'description', 'category', 'scope', 'exclusions'):
            if o.get(key):
                s[key] = o[key]
        if o.get('responsibilities'):
            s['responsibilities_admin'] = o['responsibilities']
        if o.get('scope'):
            s['scope_custom'] = True
        if isinstance(o.get('models'), list) and o['models']:
            s['models'] = [{'id': m, 'label': labels[m]} for m in o['models'] if m in labels] or spec['models']
        s['active'] = o.get('active', True) is not False
        if s['active'] or include_inactive:
            out.append(s)
    for key, o in stored['added'].items():
        if not isinstance(o, dict) or not o.get('name'):
            continue
        s = {'id': key, 'name': o['name'], 'category': o.get('category') or 'recurring', 'description': o.get('description', ''),
             'scope': o.get('scope', ''), 'responsibilities_admin': o.get('responsibilities', ''), 'exclusions': o.get('exclusions', ''),
             'models': [{'id': m, 'label': labels[m]} for m in o.get('models', []) if m in labels], 'added': True, 'scope_custom': True,
             'active': o.get('active', True) is not False}
        if s['models'] and (s['active'] or include_inactive):
            out.append(s)
    return out


def catalog_map() -> dict:
    return {s['id']: s for s in catalog()}


# ------------------------------------------------------------------ fee wording for pricing models added in the app
def _fmt(kind: str, value) -> str:
    from decimal import Decimal
    if value in (None, ''):
        return ''
    if kind == 'amount':
        return f"${Decimal(str(value)):,.2f}"
    if kind in ('percent', 'number'):
        text = f"{Decimal(str(value)):,.2f}".rstrip('0').rstrip('.')
        return text + ('%' if kind == 'percent' else '')
    if kind == 'date':
        import datetime as dt
        d = dt.date.fromisoformat(str(value))
        return f"{d.strftime('%B')} {d.day}, {d.year}"
    return str(value)


def fee_text(model_def: dict, values: dict) -> str:
    """The model's wording with the values typed in the builder. A sentence about an optional field left empty is dropped."""
    fields = {f['label'].strip().casefold(): f for f in model_def.get('fields', [])}
    sentences = re.split(r'(?<=[.;])\s+', (model_def.get('wording') or '').strip())
    out = []
    for sentence in sentences:
        missing = False
        def fill(m):
            nonlocal missing
            f = fields.get(m.group(1).strip().casefold())
            if not f:
                return m.group(0)
            text = _fmt(f['kind'], values.get(f['key']))
            if not text:
                missing = True
            return text
        filled = re.sub(r'\{([^{}]{1,80})\}', fill, sentence)
        if not missing and filled.strip():
            out.append(filled)
    return ' '.join(out) or model_def.get('label', '')


def validate_values(model_def: dict, raw: dict, name: str) -> dict:
    """The values typed for a pricing model added in the app, checked against its field definitions."""
    import server as legacy
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for f in model_def.get('fields', []):
        value = raw.get(f['key'])
        label = f"{f['label']} for {name}"
        if value in (None, ''):
            if f.get('required'):
                raise ValueError(f'Enter the {label}.')
            out[f['key']] = ''
            continue
        if f['kind'] in ('amount', 'number'):
            out[f['key']] = legacy.number(value, label, minimum=0, maximum=1000000000)
        elif f['kind'] == 'percent':
            out[f['key']] = legacy.number(value, label, minimum=0, maximum=100)
        elif f['kind'] == 'date':
            out[f['key']] = legacy.date_value(value, label)
        elif f['kind'] == 'choice':
            if str(value) not in (f.get('choices') or []):
                raise ValueError(f'Choose a valid {label}.')
            out[f['key']] = str(value)
        else:
            out[f['key']] = legacy.clean(value, label, False, 300)
    return out


# ------------------------------------------------------------------ admin screens
def _text(value, label, limit, required=False):
    import server as legacy
    return legacy.clean(value, label, required, limit)


def _key(prefix: str, name: str, taken) -> str:
    base = prefix + re.sub(r'[^a-z0-9]+', '_', name.casefold()).strip('_')[:24]
    key, n = base, 2
    while key in taken:
        key, n = f'{base}_{n}', n + 1
    return key


def _defaults(spec: dict) -> dict:
    """The standard wording of a built-in service, as editable text (what prints when nothing was changed)."""
    import json
    from pathlib import Path
    import contract_content as original
    scopes = list(original.SCOPES.get(spec['id'], original.SCOPES['other']))
    source = json.loads((Path(__file__).parent / 'templates' / 'service-scopes.json').read_text(encoding='utf-8'))
    if spec['id'] in source:
        lines = []
        for b in source[spec['id']]['scope']:
            text = b.get('text', '').replace('PRACTICE NAME', 'the Client')
            lines.append(('- ' if b.get('type') == 'bullet' else '') + text)
        scopes[0] = '\n'.join(lines)
    return {'scope': scopes[0], 'responsibilities': '\n\n'.join(x for x in scopes[1:3] if x), 'exclusions': scopes[3] if len(scopes) > 3 else ''}


def admin_view(c) -> dict:
    stored = read(c)
    used: dict = {}
    services = []
    for s in catalog(stored, include_inactive=True):
        for m in s['models']:
            used[m['id']] = used.get(m['id'], 0) + 1
        item = {k: s.get(k) for k in ('id', 'name', 'category', 'description', 'active', 'added')}
        item['models'] = [m['id'] for m in s['models']]
        if s.get('added'):
            item.update(scope=s.get('scope', ''), responsibilities=s.get('responsibilities_admin', ''), exclusions=s.get('exclusions', ''), changed=True)
        else:
            o = stored['services'].get(s['id']) or {}
            d = _defaults(s)
            item.update(scope=o.get('scope') or d['scope'], responsibilities=o.get('responsibilities') or d['responsibilities'],
                        exclusions=o.get('exclusions') or d['exclusions'], changed=bool(o))
        services.append(item)
    models = [{'id': k, 'label': v, 'builtin': True, 'used': used.get(k, 0)} for k, v in builtin_models().items()]
    models += [dict(v, id=k, builtin=False, used=used.get(k, 0)) for k, v in custom_models(stored).items()]
    return {'services': services, 'models': models, 'field_kinds': list(FIELD_KINDS)}


def _save(c, value: dict) -> None:
    if c.execute(select(db.settings.c.key).where(db.settings.c.key == KEY)).first():
        c.execute(db.settings.update().where(db.settings.c.key == KEY).values(value=value))
    else:
        c.execute(db.settings.insert().values(key=KEY, value=value))
    invalidate()


def save_service(c, actor: dict, service_id: str | None, payload: dict) -> dict:
    stored = read(c)
    labels = model_labels(stored)
    models = [m for m in dict.fromkeys(payload.get('models') or []) if m in labels]
    if not models:
        raise ValueError('Tick at least one pricing model for this service.')
    category = payload.get('category')
    if category not in ('recurring', 'on_demand'):
        raise ValueError('Choose recurring or on demand.')
    item = {'name': _text(payload.get('name'), 'Service name', 200, True), 'description': _text(payload.get('description'), 'Short description', 300),
            'category': category, 'models': models, 'active': payload.get('active', True) is not False,
            'scope': _text(payload.get('scope'), 'Scope', 6000), 'responsibilities': _text(payload.get('responsibilities'), 'Responsibilities', 6000),
            'exclusions': _text(payload.get('exclusions'), 'Exclusions', 6000)}
    base = {s['id']: s for s in _base()}
    if service_id and service_id in base:
        d = _defaults(base[service_id])
        o = {k: v for k, v in item.items()
             if not (k in ('scope', 'responsibilities', 'exclusions') and (not v or v.strip() == (d[k] or '').strip()))
             and not (k == 'name' and v == base[service_id]['name']) and not (k == 'description' and v == base[service_id].get('description'))}
        stored['services'][service_id] = o
        action = 'catalog.service_changed'
    elif service_id and service_id in stored['added']:
        if not item['scope']:
            raise ValueError('Describe the scope of the service.')
        stored['added'][service_id] = dict(stored['added'][service_id], **item)
        action = 'catalog.service_changed'
    elif not service_id:
        if not item['scope']:
            raise ValueError('Describe the scope of the service.')
        if any(s['name'].casefold() == item['name'].casefold() for s in catalog(stored, include_inactive=True)):
            raise HTTPException(409, 'A service with this name already exists.')
        service_id = _key('svc_', item['name'], set(base) | set(stored['added']))
        stored['added'][service_id] = dict(item, created=time.time(), created_by=actor['id'])
        action = 'catalog.service_added'
    else:
        raise HTTPException(404, 'Service not found.')
    _save(c, stored)
    db.log(c, actor, action, detail={'service': service_id, 'name': item['name'], 'models': models, 'active': item['active']})
    return {'id': service_id}


def save_model(c, actor: dict, model_id: str | None, payload: dict) -> dict:
    stored = read(c)
    if model_id and model_id in builtin_models():
        raise HTTPException(400, 'The standard pricing models are worded by the system and cannot be changed here. Add a new model instead.')
    label = _text(payload.get('label'), 'Name', 80, True)
    billing = payload.get('billing') if payload.get('billing') in ('recurring', 'one_time') else 'recurring'
    raw = payload.get('fields')
    if not isinstance(raw, list) or not 1 <= len(raw) <= 8:
        raise ValueError('A pricing model asks for 1 to 8 values.')
    fields, keys = [], set()
    for i, f in enumerate(raw):
        if not isinstance(f, dict):
            raise ValueError('Invalid field.')
        flabel = _text(f.get('label'), 'Field name', 60, True)
        kind = f.get('kind')
        if kind not in FIELD_KINDS:
            raise ValueError(f'Choose what kind of value "{flabel}" is.')
        key = re.sub(r'[^a-z0-9]+', '_', flabel.casefold()).strip('_')[:30] or f'f{i}'
        if key in keys:
            raise ValueError(f'Two fields are called "{flabel}".')
        keys.add(key)
        choices = [_text(x, 'Choice', 80, True) for x in (f.get('choices') or [])][:20] if kind == 'choice' else []
        if kind == 'choice' and not choices:
            raise ValueError(f'Add the choices for "{flabel}".')
        fields.append({'key': key, 'label': flabel, 'kind': kind, 'required': f.get('required') is True, 'choices': choices})
    wording = _text(payload.get('wording'), 'Wording on the agreement', 1500, True)
    known = {f['label'].casefold() for f in fields}
    unknown = [m for m in re.findall(r'\{([^{}]{1,80})\}', wording) if m.strip().casefold() not in known]
    if unknown:
        raise ValueError('The wording mentions {' + unknown[0] + '}, which is not one of the fields. Use the field names exactly.')
    if any(m.get('label', '').casefold() == label.casefold() and k != model_id for k, m in custom_models(stored).items()) \
            or any(v.casefold() == label.casefold() for v in builtin_models().values()):
        raise HTTPException(409, 'A pricing model with this name already exists.')
    value = {'label': label, 'billing': billing, 'fields': fields, 'wording': wording}
    if model_id:
        if model_id not in stored['models']:
            raise HTTPException(404, 'Pricing model not found.')
    else:
        model_id = _key('pm_', label, set(builtin_models()) | set(stored['models']))
    stored['models'][model_id] = value
    _save(c, stored)
    db.log(c, actor, 'catalog.pricing_model_saved', detail={'model': model_id, 'label': label})
    return {'id': model_id, 'preview': fee_text(value, {f['key']: {'amount': 2500, 'percent': 6, 'number': 2, 'date': '2026-12-01', 'text': 'sample', 'choice': (f['choices'] or [''])[0]}[f['kind']] for f in fields})}


def install(app) -> None:
    def super_admin(request, c):
        a = sec.get_actor(request, c)
        if a['role'] != 'super_admin':
            raise HTTPException(403, 'Only the Super Admin can change services and pricing.')
        return a

    @app.get('/api/admin/catalog')
    def api_catalog(request: Request):
        with db.engine.connect() as c:
            super_admin(request, c)
            return admin_view(c)

    @app.post('/api/admin/catalog/services')
    def api_service_add(request: Request, payload: dict = Body(...)):
        with db.engine.begin() as c:
            return save_service(c, super_admin(request, c), None, payload)

    @app.post('/api/admin/catalog/services/{service_id}')
    def api_service_save(service_id: str, request: Request, payload: dict = Body(...)):
        with db.engine.begin() as c:
            return save_service(c, super_admin(request, c), service_id[:60], payload)

    @app.post('/api/admin/catalog/models')
    def api_model_add(request: Request, payload: dict = Body(...)):
        with db.engine.begin() as c:
            return save_model(c, super_admin(request, c), None, payload)

    @app.post('/api/admin/catalog/models/{model_id}')
    def api_model_save(model_id: str, request: Request, payload: dict = Body(...)):
        with db.engine.begin() as c:
            return save_model(c, super_admin(request, c), model_id[:60], payload)

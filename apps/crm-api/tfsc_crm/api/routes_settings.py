"""Settings (§9, Director/Admin + Super Admin): domains, projects, targets, policies."""
from __future__ import annotations

import re
from datetime import timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from ..db import models as m
from ..db.session import read, tx
from ..domain import audit
from ..domain import settings as cfg
from ..ids import uuid7
from ..pacific import business_date, utcnow
from .deps import ActorDep
from .schemas import (
    DomainIn,
    DomainOut,
    DomainUpdate,
    ProjectIn,
    ProjectOut,
    ProjectUpdate,
    SettingsOut,
    SettingsPatch,
    TargetIn,
    TargetOut,
)

router = APIRouter(prefix='/api/settings', tags=['settings'])


def _domain_out(r) -> DomainOut:
    return DomainOut(id=r['id'], name=r['name'], legal_name=r['legal_name'], tfsc_entity_key=r['tfsc_entity_key'], active=r['active'],
                     sort=r['sort'], version=r['version'])


def _project_out(r, in_use: bool) -> ProjectOut:
    return ProjectOut(id=r['id'], name=r['name'], domain_id=r['domain_id'], owner_user_id=r['owner_user_id'], owner_name=r['owner_name'],
                      owner_email=r['owner_email'], owner_phone=r['owner_phone'], status=r['status'], notes=r['notes'],
                      in_use=in_use, version=r['version'])


# ------------------------------------------------------------------ domains
@router.get('/domains', response_model=list[DomainOut])
def list_domains(a: ActorDep) -> list[DomainOut]:
    if not a.can('routing.view') and not a.can('users.create'):
        raise HTTPException(403, 'You do not have permission for this action.')
    with read(a.org_id, a.id) as c:
        return [_domain_out(r) for r in c.execute(select(m.domains).order_by(m.domains.c.sort, m.domains.c.name)).mappings()]


@router.post('/domains', response_model=DomainOut)
def create_domain(body: DomainIn, a: ActorDep) -> DomainOut:
    a.require('settings.manage')
    with tx(a.org_id, a.id) as c:
        if c.execute(select(m.domains.c.id).where((m.domains.c.id == body.id) | (func.lower(m.domains.c.name) == body.name.lower()))).first():
            raise HTTPException(409, 'A domain with this id or name already exists.')
        c.execute(m.domains.insert().values(org_id=a.org_id, **body.model_dump()))
        audit.record(c, a.org_id, a.id, 'domain.created', 'domain', body.id, None, body.model_dump())
        return _domain_out(c.execute(select(m.domains).where(m.domains.c.id == body.id)).mappings().one())


@router.patch('/domains/{domain_id}', response_model=DomainOut)
def update_domain(domain_id: str, body: DomainUpdate, a: ActorDep) -> DomainOut:
    a.require('settings.manage')
    with tx(a.org_id, a.id) as c:
        r = c.execute(select(m.domains).where(m.domains.c.id == domain_id).with_for_update()).mappings().first()
        if not r:
            raise HTTPException(404, 'Domain not found.')
        if r['version'] != body.version:
            raise HTTPException(409, 'This domain changed. Refresh first.')
        vals = {k: v for k, v in body.model_dump(exclude={'version'}).items() if v is not None}
        if vals.get('active') is False and domain_id == 'scribe_align':
            raise HTTPException(400, 'Scribe Align LLC is the default domain and cannot be deactivated.')
        vals.update(version=r['version'] + 1, updated_at=utcnow())
        c.execute(update(m.domains).where(m.domains.c.id == domain_id).values(**vals))
        audit.record(c, a.org_id, a.id, 'domain.updated', 'domain', domain_id, dict(r), vals)
        return _domain_out(c.execute(select(m.domains).where(m.domains.c.id == domain_id)).mappings().one())


# ------------------------------------------------------------------ projects
def _in_use(c) -> set[str]:
    used = {r[0] for r in c.execute(select(m.users.c.default_project_id).where(m.users.c.default_project_id.is_not(None)).distinct())}
    used |= {r[0] for r in c.execute(select(m.projects.c.id).where(m.projects.c.first_used_at.is_not(None)))}
    return used


@router.get('/projects', response_model=list[ProjectOut])
def list_projects(a: ActorDep, domain_id: str | None = None) -> list[ProjectOut]:
    if not a.can('routing.view') and not a.can('users.create'):
        raise HTTPException(403, 'You do not have permission for this action.')
    with read(a.org_id, a.id) as c:
        used = _in_use(c)
        stmt = select(m.projects).order_by(m.projects.c.id)
        if domain_id:
            stmt = stmt.where((m.projects.c.domain_id == domain_id) | m.projects.c.domain_id.is_(None))
        return [_project_out(r, r['id'] in used) for r in c.execute(stmt).mappings()]


@router.get('/projects/next-id')
def next_project_id(a: ActorDep) -> dict:
    a.require('settings.manage')
    with read(a.org_id, a.id) as c:
        ids = [r[0] for r in c.execute(select(m.projects.c.id))]
    n = max((int(i[1:]) for i in ids if re.fullmatch(r'P\d{4}', i)), default=-1) + 1
    return {'id': f'P{n:04d}'}


@router.post('/projects', response_model=ProjectOut)
def create_project(body: ProjectIn, a: ActorDep) -> ProjectOut:
    a.require('settings.manage')
    with tx(a.org_id, a.id) as c:
        if c.execute(select(m.projects.c.id).where(m.projects.c.id == body.id)).first():
            raise HTTPException(409, 'This project ID already exists.')
        if body.domain_id and not c.execute(select(m.domains.c.id).where(m.domains.c.id == body.domain_id)).first():
            raise HTTPException(400, 'Unknown domain.')
        c.execute(m.projects.insert().values(org_id=a.org_id, **body.model_dump()))
        audit.record(c, a.org_id, a.id, 'project.created', 'project', body.id, None, body.model_dump(mode='json'))
        return _project_out(c.execute(select(m.projects).where(m.projects.c.id == body.id)).mappings().one(), False)


@router.patch('/projects/{project_id}', response_model=ProjectOut)
def update_project(project_id: str, body: ProjectUpdate, a: ActorDep) -> ProjectOut:
    a.require('settings.manage')
    with tx(a.org_id, a.id) as c:
        r = c.execute(select(m.projects).where(m.projects.c.id == project_id).with_for_update()).mappings().first()
        if not r:
            raise HTTPException(404, 'Project not found.')
        if r['version'] != body.version:
            raise HTTPException(409, 'This project changed. Refresh first.')
        vals = {k: v for k, v in body.model_dump(exclude={'version'}).items() if v is not None}
        if 'owner_user_id' in vals:
            owner = c.execute(select(m.users.c.display_name, m.users.c.email, m.users.c.phone).where(m.users.c.id == vals['owner_user_id'])).first()
            if not owner:
                raise HTTPException(400, 'Owner not found.')
            vals.setdefault('owner_name', owner.display_name)
            vals.setdefault('owner_email', owner.email)
        if r['status'] == 'pending_configuration' and (vals.get('owner_name') or vals.get('owner_user_id')) and 'status' not in vals:
            vals['status'] = 'active'
        vals.update(version=r['version'] + 1, updated_at=utcnow())
        c.execute(update(m.projects).where(m.projects.c.id == project_id).values(**vals))
        audit.record(c, a.org_id, a.id, 'project.updated', 'project', project_id, {k: str(r[k]) for k in vals if k in r}, {k: str(v) for k, v in vals.items()})
        return _project_out(c.execute(select(m.projects).where(m.projects.c.id == project_id)).mappings().one(), project_id in _in_use(c))


@router.get('/projects/{project_id}/summary')
def project_summary(project_id: str, a: ActorDep) -> dict:
    """Linked users, leads, clients, sign-ups (management only, §9.2)."""
    if not a.can('routing.view'):
        raise HTTPException(403, 'You do not have permission for this action.')
    with read(a.org_id, a.id) as c:
        users = c.execute(select(func.count()).select_from(m.users).where(m.users.c.default_project_id == project_id)).scalar_one()
        leads = c.execute(select(func.count()).select_from(m.leads).where(m.leads.c.project_id == project_id)).scalar_one()
        signed = c.execute(select(func.count()).select_from(m.leads).where(m.leads.c.project_id == project_id,
                                                                            m.leads.c.stage.in_(['signed_up', 'agreement_in_progress', 'client']))).scalar_one()
        clients = c.execute(select(func.count()).select_from(m.clients).where(m.clients.c.project_id == project_id)).scalar_one()
    return {'project_id': project_id, 'users': users, 'leads': leads, 'signups': signed, 'clients': clients}


# ------------------------------------------------------------------ policies & targets
@router.get('', response_model=SettingsOut)
def get_settings_values(a: ActorDep) -> SettingsOut:
    a.require('settings.manage')
    with read(a.org_id, a.id) as c:
        return SettingsOut(values={k: cfg.get(c, a.org_id, k) for k in cfg.DEFAULTS})


@router.patch('', response_model=SettingsOut)
def patch_settings(body: SettingsPatch, a: ActorDep) -> SettingsOut:
    a.require('settings.manage')
    v = body.value
    if body.key == 'lock_windows':
        if not isinstance(v, dict) or not (10 <= int(v.get('import_minutes', 15)) <= 20) or not (1 <= int(v.get('manual_minutes', 60)) <= 240):
            raise HTTPException(400, 'Import lock window must be 10-20 minutes; manual 1-240 minutes.')
        v = {'manual_minutes': int(v.get('manual_minutes', 60)), 'import_minutes': int(v['import_minutes']), 'import_min': 10, 'import_max': 20}
    elif body.key == 'celebration':
        if not isinstance(v, dict) or '{agent}' not in str(v.get('template', '')):
            raise HTTPException(400, 'The celebration text must contain {agent}.')
        v = {'template': str(v['template'])[:300], 'seconds': max(3, min(15, int(v.get('seconds', 6))))}
    elif body.key == 'dialer':
        if not isinstance(v, dict) or v.get('mode') not in ('tel', 'template', 'vbc_api'):
            raise HTTPException(400, 'Dialer mode must be tel, template or vbc_api.')
        if v['mode'] == 'template' and '{e164}' not in str(v.get('template') or ''):
            raise HTTPException(400, 'A custom dialer template must contain {e164}.')
        v = {'mode': v['mode'], 'template': v.get('template'), 'verified': bool(v.get('verified'))}
    elif body.key == 'lead_protection_days':
        v = max(7, min(365, int(v)))
    with tx(a.org_id, a.id) as c:
        before = cfg.get(c, a.org_id, body.key)
        ins = insert(m.settings).values(org_id=a.org_id, key=body.key, value=v, updated_by=a.id)
        c.execute(ins.on_conflict_do_update(index_elements=['org_id', 'key'], set_={'value': v, 'updated_by': a.id, 'updated_at': utcnow()}))
        audit.record(c, a.org_id, a.id, 'settings.updated', 'settings', body.key, {'value': before}, {'value': v})
        return SettingsOut(values={k: cfg.get(c, a.org_id, k) for k in cfg.DEFAULTS})


@router.get('/targets', response_model=list[TargetOut])
def list_targets(a: ActorDep) -> list[TargetOut]:
    with read(a.org_id, a.id) as c:
        rows = c.execute(select(m.targets).order_by(m.targets.c.effective_date.desc())).mappings().all()
    return [TargetOut(effective_date=r['effective_date'], call_target=r['call_target'], conn_target=float(r['conn_target']), bands=r['bands']) for r in rows]


@router.post('/targets', response_model=TargetOut)
def set_target(body: TargetIn, a: ActorDep) -> TargetOut:
    """Targets are effective-dated: a change applies from the next Pacific date (§20.3)."""
    a.require('settings.manage')
    eff = business_date() + timedelta(days=1)
    with tx(a.org_id, a.id) as c:
        current = cfg.target_for(c, a.org_id, eff)
        bands = body.bands or current['bands']
        ins = insert(m.targets).values(id=uuid7(), org_id=a.org_id, effective_date=eff, call_target=body.call_target, conn_target=body.conn_target,
                                       bands=bands, set_by=a.id)
        c.execute(ins.on_conflict_do_update(index_elements=['org_id', 'effective_date'],
                                            set_={'call_target': body.call_target, 'conn_target': body.conn_target, 'bands': bands, 'set_by': a.id}))
        audit.record(c, a.org_id, a.id, 'targets.updated', 'targets', eff.isoformat(), current | {'effective_date': str(current.get('effective_date'))},
                     body.model_dump())
    return TargetOut(effective_date=eff, call_target=body.call_target, conn_target=body.conn_target, bands=bands)

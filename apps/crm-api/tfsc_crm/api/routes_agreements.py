"""TFSC Agreements integration v2 on the CRM side (§19): access provisioning, pre-flight +
Agreement Lock handoff, signed event inbox, Client Profile."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from sqlalchemy import and_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from tfsc_bridge import BridgeError, validate, verify
from tfsc_refine import refine_email, refine_name, refine_phone

from .. import crypto
from ..config import get_settings
from ..db import models as m
from ..db.session import read, system_tx, tx
from ..domain import audit, duplicates, notify, outbox
from ..ids import uuid7
from ..integrations import tfsc
from ..pacific import business_date, utcnow
from ..security.roles import SUPER, SUPERVISOR, Actor, in_scope, owner_filter
from .deps import ActorDep, parse_uuid
from .schemas import Model, OkOut, Out

router = APIRouter(prefix='/api', tags=['agreements'])
TFSC_COMPANIES = ['SCRIBE ALIGN LLC', 'EXTENDED HEALTH SERVICES INC.', '24-7 CONSULTANCY LLC']
STATE_TO_STATUS = {'draft': 'draft', 'submitted': 'in_review', 'changes_requested': 'changes_requested', 'approved': 'approved',
                   'queued': 'approved', 'issued': 'issued', 'superseded': 'superseded', 'executed': 'executed'}


# ------------------------------------------------------------------ access provisioning (§19.2)
class AgreementsAccessIn(Model):
    enabled: bool
    role: Literal['user', 'admin'] = 'user'
    companies: list[Literal['SCRIBE ALIGN LLC', 'EXTENDED HEALTH SERVICES INC.', '24-7 CONSULTANCY LLC']] = Field(default_factory=list, max_length=3)
    parent_admin_tfsc_user_id: str | None = Field(default=None, max_length=64)
    link_tfsc_user_id: str | None = Field(default=None, max_length=64)


class AgreementsAccessOut(Out):
    enabled: bool
    role: str | None
    companies: list[str]
    tfsc_user_id: str | None
    status: str


@router.get('/users/{user_id}/agreements', response_model=AgreementsAccessOut)
def get_access(user_id: str, a: ActorDep) -> AgreementsAccessOut:
    a.require('agreements.grant_user')
    uid = parse_uuid(user_id)
    with read(a.org_id, a.id) as c:
        if not in_scope(c, a, uid):
            raise HTTPException(404, 'User not found.')
        g = c.execute(select(m.module_grants).where(m.module_grants.c.user_id == uid, m.module_grants.c.module == 'agreements',
                                                    m.module_grants.c.revoked_at.is_(None))).mappings().first()
    if not g:
        return AgreementsAccessOut(enabled=False, role=None, companies=[], tfsc_user_id=None, status='none')
    return AgreementsAccessOut(enabled=g['status'] == 'active', role=g['role'], companies=g['companies'], tfsc_user_id=g['external_account_id'], status=g['status'])


@router.put('/users/{user_id}/agreements', response_model=AgreementsAccessOut)
def set_access(user_id: str, body: AgreementsAccessIn, a: ActorDep) -> AgreementsAccessOut:
    """One step: toggle -> TFSC role -> companies. TFSC is authoritative; on rejection nothing is stored."""
    a.require('agreements.grant_user')
    if body.role == 'admin':
        a.require('agreements.grant_admin')
    if body.enabled and not body.companies:
        raise HTTPException(400, 'Choose at least one TFSC company.')
    uid = parse_uuid(user_id)
    with read(a.org_id, a.id) as c:
        if not in_scope(c, a, uid) or (uid == a.id and a.rank < SUPER):
            raise HTTPException(404, 'User not found.')
        u = c.execute(select(m.users).where(m.users.c.id == uid)).mappings().one()
        current = c.execute(select(m.module_grants).where(m.module_grants.c.user_id == uid, m.module_grants.c.module == 'agreements',
                                                          m.module_grants.c.revoked_at.is_(None))).mappings().first()
        oidc = c.execute(select(m.oidc_identities).where(m.oidc_identities.c.user_id == uid)).mappings().first()
    action = 'grant' if body.enabled else 'revoke'
    if action == 'revoke' and not current:
        return AgreementsAccessOut(enabled=False, role=None, companies=[], tfsc_user_id=None, status='none')
    req = {'idempotency_key': f'{uid.hex[:16]}-{action}-{hashlib.sha256(body.model_dump_json().encode()).hexdigest()[:24]}-{utcnow():%Y%m%d%H%M}',
           'action': action, 'crm_user_id': str(uid), 'granting_crm_user_id': str(a.id), 'email': u['email'], 'display_name': u['display_name'],
           'role': body.role if body.enabled else (current['role'] if current else 'user'), 'companies': list(body.companies) or (current['companies'] if current else []),
           'parent_admin_tfsc_user_id': body.parent_admin_tfsc_user_id, 'link_tfsc_user_id': body.link_tfsc_user_id,
           'oidc_issuer': oidc['issuer'] if oidc else None, 'oidc_subject': oidc['subject'] if oidc else None}
    try:
        validate('account_provision_request', req)
    except BridgeError as e:
        raise HTTPException(400, str(e)) from None
    queued = False
    try:
        resp = tfsc.provision(req)
    except tfsc.TfscError as e:
        if action == 'revoke' and e.status >= 500:
            resp, queued = {'tfsc_user_id': current['external_account_id'] if current else None, 'status': 'revoked', 'companies': []}, True
        else:
            raise HTTPException(e.status if 400 <= e.status < 500 else 502, f'TFSC Agreements did not grant access: {e}') from None
    with tx(a.org_id, a.id) as c:
        c.execute(update(m.module_grants).where(m.module_grants.c.user_id == uid, m.module_grants.c.module == 'agreements',
                                                m.module_grants.c.revoked_at.is_(None)).values(revoked_at=utcnow(), status='revoked'))
        if body.enabled:
            c.execute(m.module_grants.insert().values(id=uuid7(), org_id=a.org_id, user_id=uid, module='agreements', role=resp.get('role', body.role),
                                                      companies=resp['companies'], external_account_id=resp['tfsc_user_id'], granted_by=a.id,
                                                      parent_admin_ref=body.parent_admin_tfsc_user_id, status='active'))
        if queued:  # TFSC unreachable: CRM access is gone now; TFSC revocation retried until delivered (target <= 60 s)
            outbox.emit(c, a.org_id, 'account.provision.requested', req, key=str(uid))
        audit.record(c, a.org_id, a.id, f'agreements.{action}', 'user', uid, {'grant': dict(current) if current else None},
                     {'role': req['role'], 'companies': req['companies'], 'tfsc_user_id': resp.get('tfsc_user_id'), 'queued': queued})
    return AgreementsAccessOut(enabled=body.enabled, role=req['role'] if body.enabled else None, companies=resp['companies'] if body.enabled else [],
                               tfsc_user_id=resp.get('tfsc_user_id'), status='active' if body.enabled else 'revoked')


def relink_identity(c, user: dict, issuer: str, subject: str) -> None:
    """Granted Agreements before their first SSO sign-in: send TFSC the now-verified identity with the same grant, so
    TFSC single sign-on works without an administrator linking it by hand (delivered through the outbox)."""
    g = c.execute(select(m.module_grants).where(m.module_grants.c.user_id == user['id'], m.module_grants.c.module == 'agreements',
                                                m.module_grants.c.revoked_at.is_(None), m.module_grants.c.status == 'active')).mappings().first()
    if not g or not g['granted_by']:
        return
    req = {'idempotency_key': f"{user['id'].hex[:16]}-link-{hashlib.sha256(f'{issuer}|{subject}'.encode()).hexdigest()[:24]}",
           'action': 'grant', 'crm_user_id': str(user['id']), 'granting_crm_user_id': str(g['granted_by']), 'email': user['email'],
           'display_name': user['display_name'], 'role': g['role'], 'companies': g['companies'], 'parent_admin_tfsc_user_id': g['parent_admin_ref'],
           'link_tfsc_user_id': None, 'oidc_issuer': issuer, 'oidc_subject': subject}
    validate('account_provision_request', req)
    outbox.emit(c, user['org_id'], 'account.provision.requested', req, key=str(user['id']))


@router.get('/agreements/launch')
def launch(a: ActorDep) -> dict:
    """The Agreements ↗ menu item: only with an explicit grant (never implied by a sales role)."""
    if not a.can('agreements.open'):
        raise HTTPException(403, 'You do not have access to TFSC Agreements.')
    return {'url': tfsc.public_url() or None, 'companies': (a.agreements_grant or {}).get('companies', []), 'connected': tfsc.configured()}


# ------------------------------------------------------------------ pre-flight + handoff (§19.3)
class PreflightOut(Out):
    lead_id: uuid.UUID
    legal_name: str
    providers: list[str]
    address: str
    email: str | None
    phone: str | None
    signatory: str
    title: str
    effective_date: date
    domain: str | None
    suggested_entity: str | None
    entity_options: list[str]
    must_pick_reason: str | None
    project_id: str | None
    project_owner: str | None
    agreement_locked: bool
    agreement_status: str | None


class HandoffIn(Model):
    legal_name: str = Field(min_length=1, max_length=200)
    providers: list[str] = Field(default_factory=list, max_length=50)
    address: str = Field(default='', max_length=1000)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=60)
    signatory: str = Field(default='', max_length=200)
    title: str = Field(default='', max_length=200)
    effective_date: date
    entity: Literal['SCRIBE ALIGN LLC', 'EXTENDED HEALTH SERVICES INC.', '24-7 CONSULTANCY LLC']
    document_types: list[Literal['baa', 'services']] = Field(default=['baa', 'services'], min_length=1)


class HandoffOut(Out):
    status: Literal['opened', 'queued']
    agreements_url: str | None
    message: str


def _lead_for_agreements(c, a: Actor, lead_id: uuid.UUID, lock: bool = False) -> dict:
    if not a.can('agreements.open') or a.rank < SUPERVISOR:
        raise HTTPException(403, 'Proceeding to Agreements needs a Supervisor+ role and an Agreements grant.')
    q = select(m.leads, m.activities.c.provider_name, m.activities.c.phone_display, m.activities.c.email.label('act_email')) \
        .join(m.activities, and_(m.activities.c.id == m.leads.c.origin_activity_id, m.activities.c.business_date == m.leads.c.origin_activity_date)) \
        .where(m.leads.c.id == lead_id, m.leads.c.org_id == a.org_id)
    ld = c.execute(q.with_for_update(of=m.leads) if lock else q).mappings().first()
    if not ld or not in_scope(c, a, ld['owner_id']):
        raise HTTPException(404, 'Lead not found.')
    if ld['stage'] not in ('signed_up', 'agreement_in_progress', 'client'):
        raise HTTPException(409, 'Mark the sale as Signed Up before proceeding to Agreements.')
    return dict(ld)


@router.get('/leads/{lead_id}/agreements/preflight', response_model=PreflightOut)
def preflight(lead_id: str, a: ActorDep) -> PreflightOut:
    with read(a.org_id, a.id) as c:
        ld = _lead_for_agreements(c, a, parse_uuid(lead_id))
        dom = c.execute(select(m.domains).where(m.domains.c.id == ld['domain_id'])).mappings().first() if ld['domain_id'] else None
        proj = c.execute(select(m.projects.c.owner_name).where(m.projects.c.id == ld['project_id'])).scalar_one_or_none() if ld['project_id'] else None
    options = [x for x in TFSC_COMPANIES if x in (a.agreements_grant or {}).get('companies', [])] or ([] if a.rank < SUPER else TFSC_COMPANIES)
    suggested = dom['tfsc_entity_key'] if dom and dom['tfsc_entity_key'] in options else None
    reason = None
    if dom and not dom['tfsc_entity_key']:
        reason = f'{dom["name"]} has no TFSC contracting company. Pick one of the companies you are granted.'
    elif dom and dom['tfsc_entity_key'] not in options:
        reason = f'The domain maps to {dom["tfsc_entity_key"]}, which you are not granted. Pick a granted company or ask a Director.'
    name = refine_name(ld['provider_name'])
    return PreflightOut(lead_id=ld['id'], legal_name=name.value, providers=[name.value] if name.kind == 'person' else [], address='',
                        email=crypto.decrypt(ld['act_email']), phone=crypto.decrypt(ld['phone_display']), signatory=name.value if name.kind == 'person' else '',
                        title='', effective_date=business_date(), domain=dom['name'] if dom else None, suggested_entity=suggested,
                        entity_options=options, must_pick_reason=reason, project_id=ld['project_id'], project_owner=proj,
                        agreement_locked=ld['agreement_locked_at'] is not None, agreement_status=ld['agreement_status'])


@router.post('/leads/{lead_id}/agreements/handoff', response_model=HandoffOut)
def handoff(lead_id: str, body: HandoffIn, a: ActorDep) -> HandoffOut:
    lid = parse_uuid(lead_id)
    granted = (a.agreements_grant or {}).get('companies', [])
    if body.entity not in granted and a.rank < SUPER:
        raise HTTPException(403, 'You are not granted that TFSC company.')
    email = refine_email(body.email).value if body.email else ''
    phone = refine_phone(body.phone).display if body.phone else ''
    legal = refine_name(body.legal_name).value
    with tx(a.org_id, a.id) as c:
        ld = _lead_for_agreements(c, a, lid, lock=True)
        dom = c.execute(select(m.domains.c.name).where(m.domains.c.id == ld['domain_id'])).scalar_one_or_none() if ld['domain_id'] else None
        proj_owner = c.execute(select(m.projects.c.owner_name).where(m.projects.c.id == ld['project_id'])).scalar_one_or_none() if ld['project_id'] else None
        payload: dict[str, Any] = {
            'crm_client_id': str(lid), 'crm_lead_id': str(lid), 'crm_actor_id': str(a.id), 'entity': body.entity,
            'client': {'client_name': legal, 'legal_name': legal, 'providers': body.providers, 'address': body.address, 'client_email': email,
                       'client_phone': phone, 'client_signatory': body.signatory, 'client_title': body.title},
            'services': [], 'document_types': list(body.document_types), 'effective_date': body.effective_date.isoformat(),
            'domain': dom or '', 'project_owner': proj_owner or ''}
        if ld['project_id']:
            payload['project_id'] = ld['project_id']
        payload['idempotency_key'] = f'lead-{lid.hex[:24]}-{hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]}'
        try:
            validate('handoff_request', payload)
        except BridgeError as e:
            raise HTTPException(400, str(e)) from None
        now = utcnow()
        vals: dict = {'agreement_status': 'handoff_requested', 'version': ld['version'] + 1, 'updated_at': now}
        if not ld['agreement_locked_at']:
            vals['agreement_locked_at'] = now  # Agreement Lock: ownership, attribution and identity frozen
        if ld['stage'] == 'signed_up':
            vals.update(stage='agreement_in_progress', stage_changed_at=now)
        c.execute(update(m.leads).where(m.leads.c.id == lid).values(**vals))
        audit.record(c, a.org_id, a.id, 'agreement.handoff_requested', 'lead', lid, {'stage': ld['stage']},
                     {'entity': body.entity, 'idempotency_key': payload['idempotency_key']})
    try:
        resp = tfsc.handoff(payload)
    except tfsc.TfscError as e:
        if e.status >= 500:
            with tx(a.org_id, a.id) as c:
                outbox.emit(c, a.org_id, 'agreement.handoff.requested', payload, key=str(lid))
            return HandoffOut(status='queued', agreements_url=tfsc.public_url() or None,
                              message='TFSC Agreements is unreachable. The handoff is queued and will be delivered automatically.')
        raise HTTPException(e.status, f'TFSC Agreements rejected the handoff: {e}') from None
    url = f'{tfsc.public_url()}/reviews#redeem={resp["redeem_token"]}'
    return HandoffOut(status='opened', agreements_url=url, message='Draft created in TFSC Agreements. Complete services and pricing there.')


# ------------------------------------------------------------------ signed inbox from TFSC
@router.post('/integrations/v1/tfsc/events', include_in_schema=False)
async def tfsc_events(request: Request) -> dict:
    raw = await request.body()

    def seen(nonce: str) -> bool:
        with system_tx() as c:
            try:
                with c.begin_nested():
                    c.execute(m.inbox.insert().values(source='tfsc-nonce', event_id=nonce[:80], topic='nonce', payload={}))
                return False
            except IntegrityError:
                return True
    try:
        verify(get_settings().bridge_secret, 'POST', request.url.path, raw, dict(request.headers), seen_nonce=seen)
        env = json.loads(raw)
        validate('event_envelope', env)
    except BridgeError as e:
        raise HTTPException(e.status, str(e)) from None
    except ValueError:
        raise HTTPException(400, 'Invalid JSON.') from None
    with system_tx() as c:
        ins = insert(m.inbox).values(source='tfsc', event_id=env['event_id'], topic=env['topic'], payload=env).on_conflict_do_nothing().returning(m.inbox.c.id)
        new = c.execute(ins).first()
    if not new:
        return {'ok': True, 'duplicate': True}
    try:
        process_event(env)
        with system_tx() as c:
            c.execute(update(m.inbox).where(m.inbox.c.id == new[0]).values(processed_at=utcnow()))
    except Exception as e:
        with system_tx() as c:
            c.execute(update(m.inbox).where(m.inbox.c.id == new[0]).values(error=str(e)[:2000]))
        raise
    return {'ok': True}


def process_event(env: dict) -> None:
    topic, data = env['topic'], env['data']
    if topic.startswith('agreement.'):
        validate('agreement_event_data', data)
        try:
            lid = uuid.UUID(data['crm_client_id'])
        except ValueError:
            return
        with system_tx() as c:
            org = c.execute(select(m.leads.c.org_id).where(m.leads.c.id == lid)).scalar_one_or_none()
        if org is None:
            return
        with tx(org) as c:
            ld = c.execute(select(m.leads).where(m.leads.c.id == lid).with_for_update()).mappings().one()
            status = STATE_TO_STATUS.get(data['state'], data['state'])
            vals: dict = {'agreement_status': status, 'updated_at': utcnow()}
            if data['state'] == 'issued' and ld['stage'] in ('signed_up', 'agreement_in_progress'):
                vals.update(stage='client', stage_changed_at=utcnow())
            c.execute(update(m.leads).where(m.leads.c.id == lid).values(**vals))
            audit.record(c, org, None, topic, 'lead', lid, {'agreement_status': ld['agreement_status']},
                         {'state': data['state'], 'review_id': data.get('review_id'), 'files': data.get('files', []), 'event_id': env['event_id']})
            if data['state'] == 'issued':
                _upsert_client(c, org, dict(ld), data)
                duplicates.set_state(c, org, ld['phone_key'], 'client', ld['owner_id'], lid, only_upgrade=False)
            notify.notify(c, org, [ld['supervisor_id'], ld['qualified_by']], f'agreement.{data["state"]}', {'lead_id': str(lid), 'state': data['state']})
    elif topic in ('account.suspended', 'account.revoked'):
        crm_user = data.get('crm_user_id')
        if crm_user:
            with system_tx() as c:
                c.execute(update(m.module_grants).where(m.module_grants.c.user_id == uuid.UUID(crm_user), m.module_grants.c.module == 'agreements',
                                                        m.module_grants.c.revoked_at.is_(None)).values(status='revoked', revoked_at=utcnow()))


def _upsert_client(c, org: uuid.UUID, ld: dict, data: dict) -> None:
    act = c.execute(select(m.activities).where(m.activities.c.id == ld['origin_activity_id'], m.activities.c.business_date == ld['origin_activity_date'])).mappings().one()
    vals = dict(org_id=org, lead_id=ld['id'], prospect_id=ld['prospect_id'], display_name=act['provider_name'], legal_name=act['provider_name'],
                domain_id=ld['domain_id'], project_id=ld['project_id'], tfsc_client_id=data['tfsc_client_id'], agreement_status='issued',
                primary_contact={'phone': crypto.decrypt(act['phone_display']), 'email': crypto.decrypt(act['email'])},
                account_owner_id=ld['owner_id'], supervisor_id=ld['supervisor_id'], sale_date=(ld['signed_at'] or utcnow()).date())
    ins = insert(m.clients).values(id=uuid7(), **vals)
    c.execute(ins.on_conflict_do_update(index_elements=['lead_id'], set_={'tfsc_client_id': data['tfsc_client_id'], 'agreement_status': 'issued', 'updated_at': utcnow()}))


# ------------------------------------------------------------------ Client Profile (§19.4)
class ClientOut(Out):
    id: uuid.UUID
    lead_id: uuid.UUID | None
    display_name: str
    legal_name: str | None
    account_owner_id: uuid.UUID | None
    account_owner: str | None
    sale_date: date | None
    agreement_status: str | None
    primary_contact: dict[str, Any]


class ClientDetailOut(ClientOut):
    supervisor_id: uuid.UUID | None
    domain_id: str | None
    project_id: str | None
    project_owner: str | None
    tfsc_client_id: str | None
    agreements: list[dict[str, Any]]
    meetings: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    attribution: list[dict[str, Any]]
    files_tab: str


@router.get('/clients', response_model=list[ClientOut])
def list_clients(a: ActorDep, q: str | None = None) -> list[ClientOut]:
    cl = m.clients
    with read(a.org_id, a.id) as c:
        stmt = select(cl, m.users.c.display_name.label('owner')).outerjoin(m.users, m.users.c.id == cl.c.account_owner_id).where(owner_filter(a, cl.c.account_owner_id))
        if q and a.rank >= SUPERVISOR:
            stmt = stmt.where(cl.c.display_name.ilike(f'%{q[:80]}%'))
        rows = c.execute(stmt.order_by(cl.c.created_at.desc()).limit(500)).mappings().all()
    return [ClientOut(id=r['id'], lead_id=r['lead_id'], display_name=r['display_name'], legal_name=r['legal_name'], account_owner_id=r['account_owner_id'],
                      account_owner=r['owner'], sale_date=r['sale_date'], agreement_status=r['agreement_status'], primary_contact=r['primary_contact'])
            for r in rows]


@router.get('/clients/{client_id}', response_model=ClientDetailOut | ClientOut)
def client_detail(client_id: str, a: ActorDep):
    from .routes_pipeline import timeline
    cid = parse_uuid(client_id)
    with read(a.org_id, a.id) as c:
        r = c.execute(select(m.clients, m.users.c.display_name.label('owner')).outerjoin(m.users, m.users.c.id == m.clients.c.account_owner_id)
                      .where(m.clients.c.id == cid, owner_filter(a, m.clients.c.account_owner_id))).mappings().first()
        if not r:
            raise HTTPException(404, 'Client not found.')
        base = dict(id=r['id'], lead_id=r['lead_id'], display_name=r['display_name'], legal_name=r['legal_name'], account_owner_id=r['account_owner_id'],
                    account_owner=r['owner'], sale_date=r['sale_date'], agreement_status=r['agreement_status'], primary_contact=r['primary_contact'])
        if a.rank < SUPERVISOR:  # agents: own clients, non-restricted fields only
            return ClientOut(**base)
        ld = dict(c.execute(select(m.leads).where(m.leads.c.id == r['lead_id'])).mappings().one()) if r['lead_id'] else None
        agreements = [{'event': x['action'], 'at': x['at'].isoformat(), 'state': (x['after'] or {}).get('state'), 'files': [
            {**f, 'download_url': f'{tfsc.public_url()}/files/{f["id"]}'} for f in (x['after'] or {}).get('files', [])]}
            for x in c.execute(select(m.audit_log).where(m.audit_log.c.resource == 'lead', m.audit_log.c.resource_id == str(r['lead_id']),
                                                          m.audit_log.c.action.like('agreement.%')).order_by(m.audit_log.c.id)).mappings()]
        meetings = [{'title': x['title'], 'starts_at': x['starts_at'].isoformat(), 'status': x['status']}
                    for x in c.execute(select(m.meetings).where(m.meetings.c.lead_id == r['lead_id'])).mappings()] if r['lead_id'] else []
        attribution = [{'user': x['display_name'], 'role': x['role'], 'share': float(x['share'])} for x in c.execute(
            select(m.attribution_ledger, m.users.c.display_name).join(m.users, m.users.c.id == m.attribution_ledger.c.user_id)
            .where(m.attribution_ledger.c.lead_id == r['lead_id'])).mappings()] if r['lead_id'] else []
        tl = timeline(c, a, ld) if ld else []
        routing = a.can('routing.view')
        proj_owner = c.execute(select(m.projects.c.owner_name).where(m.projects.c.id == r['project_id'])).scalar_one_or_none() if r['project_id'] and routing else None
    return ClientDetailOut(**base, supervisor_id=r['supervisor_id'], domain_id=r['domain_id'] if routing else None, project_id=r['project_id'] if routing else None,
                           project_owner=proj_owner, tfsc_client_id=r['tfsc_client_id'], agreements=agreements, meetings=meetings, timeline=tl,
                           attribution=attribution, files_tab='Client file storage is deferred by the owner (placeholder).')


__all__ = ['OkOut']

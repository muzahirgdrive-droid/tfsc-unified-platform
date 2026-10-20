"""TFSC side of the CRM v2 integration (master command §19).

Additive module: legal content, templates, review gate and document pipeline are untouched.
- POST /api/integrations/v1/crm/accounts — one-step create-or-link of an agreements account,
  requested by an authorized CRM Director/Super Admin (§19.2). TFSC stays authoritative: the
  granting admin's own TFSC rights must cover the grant (intersection rule); 'admin' only when
  the granter is a TFSC super_admin; 'super_admin' is never provisioned from the CRM.
- Signed event outbox to the CRM inbox (§19.3): agreement.draft_created / submitted /
  changes_requested / approved / issued / superseded, account.suspended / revoked.
Signatures use the shared packages/bridge-contract implementation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time

from fastapi import HTTPException, Request
from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, String, Table, Text, delete, select, update

import database as db
import security as sec
from entities import ENTITIES

try:
    from tfsc_bridge import BridgeError, canonical_body, headers, validate, verify
except ImportError:  # pragma: no cover - bridge package must be installed (pip install -e packages/bridge-contract/python)
    raise

log = logging.getLogger('tfsc.crm_v2')

account_links = Table('crm_account_links', db.meta,
                      Column('crm_user_id', String(64), primary_key=True),
                      Column('tfsc_user_id', String(32), ForeignKey('users.id'), nullable=False, index=True),
                      Column('status', String(20), nullable=False),
                      Column('granted_by_crm_user_id', String(64), nullable=False),
                      Column('created', Float, nullable=False), Column('updated', Float, nullable=False))
provision_requests = Table('crm_provision_requests', db.meta,
                           Column('idempotency_key', String(80), primary_key=True),
                           Column('body_hash', String(64), nullable=False),
                           Column('response', JSON, nullable=False), Column('created', Float, nullable=False))
outbox = Table('tfsc_outbox', db.meta,
               Column('id', String(32), primary_key=True), Column('event_id', String(64), nullable=False, unique=True),
               Column('topic', String(60), nullable=False), Column('payload', JSON, nullable=False),
               Column('state', String(12), nullable=False), Column('attempts', Integer, nullable=False, default=0),
               Column('next_attempt', Float, nullable=False, index=True), Column('last_error', Text),
               Column('created', Float, nullable=False))


def _secret() -> str:
    return os.getenv('TFSC_CRM_SHARED_SECRET', '')


def _digest(payload) -> str:
    return hashlib.sha256(canonical_body(payload)).hexdigest()


async def _verified_body(request: Request, c) -> dict:
    from crm_bridge import nonces
    raw = await request.body()

    def seen(nonce: str) -> bool:
        c.execute(delete(nonces).where(nonces.c.created < time.time() - 600))
        if c.execute(select(nonces).where(nonces.c.nonce == nonce)).first():
            return True
        c.execute(nonces.insert().values(nonce=nonce, created=time.time()))
        return False
    try:
        verify(_secret(), request.method, request.url.path, raw, dict(request.headers), seen_nonce=seen)
        body = json.loads(raw)
    except BridgeError as e:
        raise HTTPException(e.status, str(e)) from None
    except ValueError:
        raise HTTPException(400, 'Invalid JSON.') from None
    return body


def _tfsc_actor_for_crm(c, crm_user_id: str):
    """Resolve the TFSC account linked to a CRM user (provisioned link or legacy actor mapping)."""
    from crm_bridge import actors, current_actor
    link = c.execute(select(account_links).where(account_links.c.crm_user_id == crm_user_id, account_links.c.status == 'active')).mappings().first()
    uid = link['tfsc_user_id'] if link else c.execute(select(actors.c.user_id).where(actors.c.crm_actor_id == crm_user_id.replace('-', ''))).scalar_one_or_none()
    if not uid:
        return None
    return current_actor(c, uid)


def _kill_sessions(c, uid: str) -> None:
    c.execute(delete(db.sessions).where(db.sessions.c.user_id == uid))


def provision(c, body: dict) -> dict:
    try:
        validate('account_provision_request', body)
    except BridgeError as e:
        raise HTTPException(400, str(e)) from None
    prev = c.execute(select(provision_requests).where(provision_requests.c.idempotency_key == body['idempotency_key'])).mappings().first()
    if prev:
        if prev['body_hash'] != _digest(body):
            raise HTTPException(409, 'Idempotency key belongs to a different request.')
        return prev['response']
    granter = _tfsc_actor_for_crm(c, body['granting_crm_user_id'])
    if not granter or granter['role'] not in ('admin', 'super_admin'):
        raise HTTPException(403, 'The granting CRM user has no TFSC administrator account. Link it first.')
    companies = list(dict.fromkeys(body['companies']))
    if any(x not in ENTITIES for x in companies):
        raise HTTPException(400, 'Unknown contracting company.')
    if granter['role'] != 'super_admin':
        if body['role'] != 'user':
            raise HTTPException(403, 'Only a TFSC Super Admin can grant the admin role.')
        missing = [x for x in companies if x not in granter['companies']]
        if missing:
            raise HTTPException(403, 'Your own TFSC company rights do not cover: ' + ', '.join(missing))
    link = c.execute(select(account_links).where(account_links.c.crm_user_id == body['crm_user_id'])).mappings().first()
    now = time.time()
    created = False
    action = body['action']
    if action in ('suspend', 'revoke'):
        if not link:
            raise HTTPException(404, 'This CRM user has no agreements account link.')
        target = c.execute(select(db.users).where(db.users.c.id == link['tfsc_user_id'])).mappings().one()
        if granter['role'] != 'super_admin' and not sec.can_manage(granter, target):
            raise HTTPException(403, 'This account is outside your TFSC administration scope.')
        c.execute(update(db.users).where(db.users.c.id == target['id']).values(active=False))
        status = 'suspended' if action == 'suspend' else 'revoked'
        c.execute(update(account_links).where(account_links.c.crm_user_id == body['crm_user_id']).values(status=status, updated=now))
        _kill_sessions(c, target['id'])
        db.log(c, granter, f'crm.account_{status}', detail={'tfsc_user_id': target['id'], 'crm_user_id': body['crm_user_id']})
        resp = {'tfsc_user_id': target['id'], 'status': status, 'companies': target['companies'], 'role': target['role'], 'created': False}
    else:
        parent = body.get('parent_admin_tfsc_user_id') or (granter['id'] if granter['role'] == 'admin' else None)
        if parent:
            p = c.execute(select(db.users).where(db.users.c.id == parent, db.users.c.active.is_(True))).mappings().first()
            if not p or p['role'] not in ('admin', 'super_admin'):
                raise HTTPException(400, 'Parent administrator not found.')
        target_id = link['tfsc_user_id'] if link else body.get('link_tfsc_user_id')
        if target_id:
            target = c.execute(select(db.users).where(db.users.c.id == target_id)).mappings().first()
            if not target:
                raise HTTPException(404, 'TFSC account to link was not found.')
            if target['role'] == 'super_admin':
                raise HTTPException(403, 'A TFSC Super Admin account is never managed from the CRM.')
            if granter['role'] != 'super_admin' and not (sec.can_manage(granter, target) or target['admin_id'] is None and target['role'] == 'user'):
                raise HTTPException(403, 'This account is outside your TFSC administration scope.')
            c.execute(update(db.users).where(db.users.c.id == target_id).values(
                role=body['role'], companies=companies, active=True, name=body['display_name'][:150],
                admin_id=parent if body['role'] == 'user' else None))
        else:
            if c.execute(select(db.users.c.id).where(db.users.c.email == body['email'].lower())).first():
                raise HTTPException(409, 'A TFSC account with this email exists. Choose it explicitly to link (never matched by email alone).')
            target_id = db.uid()
            c.execute(db.users.insert().values(id=target_id, email=body['email'].lower(), name=body['display_name'][:150],
                                               password=sec.password_hash(secrets.token_urlsafe(32)), role=body['role'],
                                               admin_id=parent if body['role'] == 'user' else None, companies=companies, active=True,
                                               reset_required=True, created=now))
            created = True
        if link:
            c.execute(update(account_links).where(account_links.c.crm_user_id == body['crm_user_id']).values(
                tfsc_user_id=target_id, status='active', updated=now, granted_by_crm_user_id=body['granting_crm_user_id']))
        else:
            c.execute(account_links.insert().values(crm_user_id=body['crm_user_id'], tfsc_user_id=target_id, status='active',
                                                    granted_by_crm_user_id=body['granting_crm_user_id'], created=now, updated=now))
        if body.get('oidc_issuer') and body.get('oidc_subject'):
            import oidc_support
            acc = oidc_support.accounts
            try:
                with c.begin_nested():  # optional: a failure here must not abort the provisioning transaction
                    c.execute(delete(acc).where(acc.c.issuer == body['oidc_issuer'], acc.c.subject == body['oidc_subject']))
                    c.execute(acc.insert().values(issuer=body['oidc_issuer'], subject=body['oidc_subject'], user_id=target_id))
            except Exception:  # noqa: BLE001 - an admin can still link the identity on /reviews
                log.exception('OIDC link skipped for %s', target_id)
        db.log(c, granter, 'crm.account_provisioned', detail={'tfsc_user_id': target_id, 'crm_user_id': body['crm_user_id'],
                                                              'role': body['role'], 'companies': companies, 'created': created})
        resp = {'tfsc_user_id': target_id, 'status': 'active', 'companies': companies, 'role': body['role'], 'created': created}
    c.execute(provision_requests.insert().values(idempotency_key=body['idempotency_key'], body_hash=_digest(body), response=resp, created=now))
    return resp


# ------------------------------------------------------------------ events to the CRM
def emit(c, client_id: str, topic: str, state: str, review: dict | None = None, files: list | None = None) -> None:
    """Queue a signed event for every CRM client mapped to this TFSC client (no-op for non-CRM clients)."""
    from crm_bridge import links
    rows = c.execute(select(links.c.crm_client_id).where(links.c.client_id == client_id)).all()
    for (crm_client_id,) in rows:
        event_id = db.uid()
        data = {'crm_client_id': crm_client_id, 'tfsc_client_id': client_id, 'state': state,
                'review_id': review['id'] if review else None, 'entity': review['entity'] if review else '',
                'revision': int(review['version']) if review else 0,
                'files': [{'id': f['id'], 'name': f['name'], 'sha256': f['sha256']} for f in files or []]}
        envelope = {'event_id': event_id, 'topic': topic, 'schema_version': 1, 'source': 'tfsc',
                    'occurred_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'data': data}
        c.execute(outbox.insert().values(id=db.uid(), event_id=event_id, topic=topic, payload=envelope, state='pending', attempts=0,
                                         next_attempt=time.time(), created=time.time()))
    _wake.set()


def deliver_pending(limit: int = 100) -> int:
    url = os.getenv('TFSC_CRM_URL', '').rstrip('/')
    if not url or len(_secret()) < 32:
        return 0
    import httpx
    from urllib.parse import urlsplit
    internal = os.getenv('TFSC_CRM_INTERNAL_URL', '').rstrip('/') or url
    path = '/api/integrations/v1/tfsc/events'
    with db.engine.begin() as c:
        q = select(outbox).where(outbox.c.state == 'pending', outbox.c.next_attempt <= time.time()).order_by(outbox.c.created).limit(limit)
        if db.engine.dialect.name == 'postgresql':
            q = q.with_for_update(skip_locked=True)
        rows = [dict(r) for r in c.execute(q).mappings()]
        for r in rows:
            body = canonical_body(r['payload'])
            try:
                h = headers(_secret(), 'POST', path, body, event_id=r['event_id'], schema=f"{r['topic']}@1")
                # In containers the CRM is reached on an internal URL; its Host check still expects the public origin.
                h['Host'] = urlsplit(url).netloc
                resp = httpx.post(internal + path, content=body, headers=h, timeout=10)
                if resp.status_code >= 300 and resp.status_code != 409:
                    raise RuntimeError(f'CRM inbox HTTP {resp.status_code}: {resp.text[:200]}')
                c.execute(update(outbox).where(outbox.c.id == r['id']).values(state='delivered', attempts=r['attempts'] + 1))
            except Exception as e:  # noqa: BLE001
                attempts = r['attempts'] + 1
                c.execute(update(outbox).where(outbox.c.id == r['id']).values(
                    attempts=attempts, state='dead' if attempts >= 12 else 'pending', last_error=str(e)[:2000],
                    next_attempt=time.time() + min(3600, 2 ** attempts)))
    return len(rows)


_wake = threading.Event()
_started = False


def start_delivery_thread() -> None:
    global _started
    if _started or not os.getenv('TFSC_CRM_URL'):
        return
    _started = True

    def loop():
        while True:
            try:
                if not deliver_pending():
                    _wake.wait(2)
                    _wake.clear()
            except Exception:  # noqa: BLE001
                log.exception('tfsc outbox delivery failed')
                time.sleep(5)
    threading.Thread(target=loop, name='tfsc-crm-outbox', daemon=True).start()


def install(app) -> None:
    @app.post('/api/integrations/v1/crm/accounts')
    async def provision_account(request: Request):
        with db.engine.begin() as c:
            body = await _verified_body(request, c)
            return provision(c, body)

    start_delivery_thread()  # no-op unless TFSC_CRM_URL is configured

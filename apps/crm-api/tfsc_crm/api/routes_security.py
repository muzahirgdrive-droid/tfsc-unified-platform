"""Security Center (§21, §23 Super Admin): overview, audit log + verification, anchors, sessions, scheduler runs,
outbox dead letters; one-click offboarding; approval-logged exports. Director: read. Super Admin: manage."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import Field
from sqlalchemy import ColumnElement, func, select, update

from ..db import models as m
from ..db.session import read, system_tx, tx
from ..domain import audit, exports, offboarding
from ..pacific import utcnow
from ..realtime import broker
from ..security.roles import DIRECTOR
from .deps import ActorDep, parse_uuid
from .schemas import Model, OkOut, Out

router = APIRouter(tags=['security'])
SECURITY_FLAG_KINDS = ('scraping_suspected', 'duplicate_lead', 'import_contradiction')


class CountOut(Out):
    key: str
    count: int


class SecurityOverviewOut(Out):
    open_red_flags: list[CountOut]
    scraping_suspected_7d: int
    failed_logins_24h: int
    failed_logins_by_user: list[CountOut]
    active_sessions: int
    supervisors_without_sso: int
    offboarded_30d: int
    exports_pending: int
    exports_ready: int
    outbox_pending: int
    outbox_dead: int
    sse_connections: int
    audit_rows: int
    last_anchor: dict[str, Any] | None
    jobs: list[dict[str, Any]]


class AuditRowOut(Out):
    id: int
    at: datetime
    actor: str | None
    action: str
    resource: str
    resource_id: str | None
    ip: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class AuditPageOut(Out):
    items: list[AuditRowOut]
    next_cursor: int | None


class VerifyOut(Out):
    ok: bool
    rows: int
    broken_links: list[int]
    altered_rows: list[int]
    anchors: int
    anchors_failed: list[str]
    head: str | None


class SessionRowOut(Out):
    id: str
    user_id: str
    user: str
    created_at: datetime
    last_seen_at: datetime
    ip: str | None
    user_agent: str | None


class OffboardIn(Model):
    reason: str = Field(min_length=3, max_length=500)


class OffboardOut(Out):
    sessions_revoked: int
    followups_moved: int
    leads_moved: int
    agreements_revoked: bool
    queue_owner: str


class ExportIn(Model):
    kind: Literal['activities', 'leads']
    start: date
    end: date
    reason: str = Field(min_length=5, max_length=1000)


class ExportDecisionIn(Model):
    approve: bool
    note: str = Field(default='', max_length=500)


class ExportOut(Out):
    id: str
    kind: str
    status: str
    start: str
    end: str
    reason: str | None
    requested_by: str
    requested_by_name: str
    approved_by_name: str | None
    row_count: int | None
    expires_at: datetime | None
    password_revealed: bool
    downloads: int
    error: str | None
    created_at: datetime


class PasswordOut(Out):
    password: str


class DeadLetterOut(Out):
    id: int
    topic: str
    attempts: int
    last_error: str | None
    created_at: datetime


# ------------------------------------------------------------------ overview
@router.get('/api/security/overview', response_model=SecurityOverviewOut)
def overview(a: ActorDep) -> SecurityOverviewOut:
    a.require('security.read')
    now = utcnow()
    f, al, u, s = m.flags, m.audit_log, m.users, m.user_sessions
    with read(a.org_id, a.id) as c:
        red = c.execute(select(f.c.kind, func.count()).where(f.c.severity == 'red', f.c.status != 'resolved').group_by(f.c.kind)).all()
        scraping = c.execute(select(func.count()).select_from(f).where(f.c.kind == 'scraping_suspected', f.c.created_at >= now - timedelta(days=7))).scalar_one()
        failed = c.execute(select(al.c.actor_id, func.count()).where(al.c.action == 'auth.login_failed', al.c.at >= now - timedelta(hours=24))
                           .group_by(al.c.actor_id).order_by(func.count().desc()).limit(10)).all()
        names = dict(c.execute(select(u.c.id, u.c.display_name).where(u.c.id.in_([r[0] for r in failed if r[0]] or [uuid.UUID(int=0)]))).all())
        no_sso = c.execute(select(func.count()).select_from(u).where(
            u.c.status == 'active', u.c.crm_role.in_(('supervisor', 'manager', 'director_admin', 'super_admin')),
            ~u.c.id.in_(select(m.oidc_identities.c.user_id)))).scalar_one()
        offb = c.execute(select(func.count()).select_from(u).where(u.c.status == 'offboarded', u.c.updated_at >= now - timedelta(days=30))).scalar_one()
        ex: dict[str, int] = dict(c.execute(select(m.exports.c.status, func.count()).group_by(m.exports.c.status)).all())
        audit_rows = c.execute(select(func.count()).select_from(al)).scalar_one()
        last = c.execute(select(m.audit_anchors).order_by(m.audit_anchors.c.id.desc()).limit(1)).mappings().first()
    with system_tx() as c:
        active = c.execute(select(func.count()).select_from(s).join(u, u.c.id == s.c.user_id).where(
            u.c.org_id == a.org_id, s.c.revoked_at.is_(None), s.c.expires_at > now, s.c.idle_expires_at > now)).scalar_one()
        ob: dict[str, int] = dict(c.execute(select(m.outbox.c.state, func.count()).where(m.outbox.c.org_id == a.org_id).group_by(m.outbox.c.state)).all())
        jobs = [dict(r) for r in c.execute(select(m.job_runs.c.job, func.max(m.job_runs.c.finished_at).label('last_run'))
                                            .group_by(m.job_runs.c.job).order_by(m.job_runs.c.job)).mappings()]
    try:
        sse = broker().connections()
    except Exception:
        sse = 0
    return SecurityOverviewOut(
        open_red_flags=[CountOut(key=k, count=n) for k, n in red], scraping_suspected_7d=scraping,
        failed_logins_24h=sum(n for _u, n in failed), failed_logins_by_user=[CountOut(key=names.get(uid, 'unknown'), count=n) for uid, n in failed],
        active_sessions=active, supervisors_without_sso=no_sso, offboarded_30d=offb, exports_pending=ex.get('requested', 0), exports_ready=ex.get('ready', 0),
        outbox_pending=ob.get('pending', 0), outbox_dead=ob.get('dead', 0), sse_connections=sse, audit_rows=audit_rows,
        last_anchor={'day': last['anchored_for'].isoformat(), 'rows': last['rows'], 'sink': last['sink'], 'head': last['head_hash'][:16]} if last else None,
        jobs=[{'job': j['job'], 'last_run': j['last_run'].isoformat() if j['last_run'] else None} for j in jobs])


# ------------------------------------------------------------------ audit
@router.get('/api/security/audit', response_model=AuditPageOut)
def audit_log(a: ActorDep, action: str | None = None, resource: str | None = None, actor_id: str | None = None, cursor: int | None = None,
              limit: int = 50) -> AuditPageOut:
    a.require('security.read')
    limit = max(1, min(limit, 200))
    al, u = m.audit_log, m.users
    cond: list[ColumnElement[bool]] = []
    if action:
        cond.append(al.c.action.like(action.replace('%', '') + '%'))
    if resource:
        cond.append(al.c.resource == resource)
    if actor_id:
        cond.append(al.c.actor_id == parse_uuid(actor_id))
    if cursor is not None:
        cond.append(al.c.id < cursor)
    with read(a.org_id, a.id) as c:
        rows = c.execute(select(al, u.c.display_name).outerjoin(u, u.c.id == al.c.actor_id).where(*cond)
                         .order_by(al.c.id.desc()).limit(limit + 1)).mappings().all()
    items = [AuditRowOut(id=r['id'], at=r['at'], actor=r['display_name'], action=r['action'], resource=r['resource'], resource_id=r['resource_id'],
                         ip=str(r['ip']) if r['ip'] else None, before=r['before'], after=r['after']) for r in rows[:limit]]
    return AuditPageOut(items=items, next_cursor=rows[limit - 1]['id'] if len(rows) > limit else None)


@router.post('/api/security/audit/verify', response_model=VerifyOut)
def verify(a: ActorDep) -> VerifyOut:
    a.require('security.manage')
    with read(a.org_id, a.id) as c:
        res = audit.verify_chain(c, a.org_id)
    with tx(a.org_id, a.id) as c:
        audit.record(c, a.org_id, a.id, 'audit.verified', 'audit_log', None, None, {'ok': res['ok'], 'rows': res['rows']})
    return VerifyOut(**res)


@router.get('/api/security/anchors', response_model=list[dict])
def anchors(a: ActorDep) -> list[dict]:
    a.require('security.read')
    with read(a.org_id, a.id) as c:
        rows = c.execute(select(m.audit_anchors).order_by(m.audit_anchors.c.id.desc()).limit(60)).mappings().all()
    return [{'day': r['anchored_for'].isoformat(), 'rows': r['rows'], 'last_audit_id': r['last_audit_id'], 'head_hash': r['head_hash'],
             'sink': r['sink'], 'created_at': r['created_at'].isoformat()} for r in rows]


# ------------------------------------------------------------------ sessions
@router.get('/api/security/sessions', response_model=list[SessionRowOut])
def sessions(a: ActorDep, user_id: str | None = None) -> list[SessionRowOut]:
    a.require('security.read')
    s, u = m.user_sessions, m.users
    now = utcnow()
    cond = [u.c.org_id == a.org_id, s.c.revoked_at.is_(None), s.c.expires_at > now, s.c.idle_expires_at > now]
    if user_id:
        cond.append(s.c.user_id == parse_uuid(user_id))
    with system_tx() as c:
        rows = c.execute(select(s.c.token_hash, s.c.user_id, u.c.display_name, s.c.created_at, s.c.last_seen_at, s.c.ip, s.c.user_agent)
                         .join(u, u.c.id == s.c.user_id).where(*cond).order_by(s.c.last_seen_at.desc()).limit(500)).mappings().all()
    return [SessionRowOut(id=r['token_hash'][:16], user_id=str(r['user_id']), user=r['display_name'], created_at=r['created_at'],
                          last_seen_at=r['last_seen_at'], ip=str(r['ip']) if r['ip'] else None, user_agent=r['user_agent']) for r in rows]


@router.delete('/api/security/sessions/{sid}', response_model=OkOut)
def revoke_session(sid: str, a: ActorDep) -> OkOut:
    a.require('security.manage')
    if len(sid) != 16:
        raise HTTPException(404, 'Session not found.')
    s, u = m.user_sessions, m.users
    with system_tx() as c:
        row = c.execute(select(s.c.token_hash, s.c.user_id).join(u, u.c.id == s.c.user_id)
                        .where(u.c.org_id == a.org_id, s.c.token_hash.like(sid + '%'), s.c.revoked_at.is_(None))).first()
        if not row:
            raise HTTPException(404, 'Session not found.')
        c.execute(update(s).where(s.c.token_hash == row[0]).values(revoked_at=utcnow()))
    with tx(a.org_id, a.id) as c:
        audit.record(c, a.org_id, a.id, 'session.revoked', 'user', row[1], None, {'session': sid})
    return OkOut()


# ------------------------------------------------------------------ outbox dead letters
@router.get('/api/security/outbox/dead', response_model=list[DeadLetterOut])
def dead_letters(a: ActorDep) -> list[DeadLetterOut]:
    a.require('security.read')
    with system_tx() as c:
        rows = c.execute(select(m.outbox.c.id, m.outbox.c.topic, m.outbox.c.attempts, m.outbox.c.last_error, m.outbox.c.created_at)
                         .where(m.outbox.c.org_id == a.org_id, m.outbox.c.state == 'dead').order_by(m.outbox.c.id.desc()).limit(200)).mappings().all()
    return [DeadLetterOut(**dict(r)) for r in rows]


@router.post('/api/security/outbox/{event_id}/retry', response_model=OkOut)
def retry_dead(event_id: str, a: ActorDep) -> OkOut:
    a.require('security.manage')
    if not event_id.isdigit():
        raise HTTPException(404, 'Event not found.')
    with system_tx() as c:
        n = c.execute(update(m.outbox).where(m.outbox.c.id == int(event_id), m.outbox.c.org_id == a.org_id, m.outbox.c.state == 'dead')
                      .values(state='pending', attempts=0, next_attempt_at=utcnow())).rowcount
    if not n:
        raise HTTPException(404, 'Event not found.')
    with tx(a.org_id, a.id) as c:
        audit.record(c, a.org_id, a.id, 'outbox.retried', 'outbox', event_id)
    return OkOut()


# ------------------------------------------------------------------ offboarding
@router.post('/api/users/{user_id}/offboard', response_model=OffboardOut)
def offboard(user_id: str, body: OffboardIn, a: ActorDep) -> OffboardOut:
    uid = parse_uuid(user_id)
    with tx(a.org_id, a.id) as c:
        return OffboardOut(**offboarding.offboard(c, a, uid, body.reason))


# ------------------------------------------------------------------ exports
def _export_out(r: dict, names: dict) -> ExportOut:
    return ExportOut(id=str(r['id']), kind=r['kind'], status=r['status'], start=r['filters']['start'], end=r['filters']['end'], reason=r['reason'],
                     requested_by=str(r['requested_by']), requested_by_name=names.get(r['requested_by'], ''), approved_by_name=names.get(r['approved_by']),
                     row_count=r['row_count'], expires_at=r['expires_at'], password_revealed=r['password_revealed_at'] is not None,
                     downloads=r['downloads'], error=r['error'], created_at=r['created_at'])


@router.post('/api/exports', response_model=ExportOut, status_code=201)
def request_export(body: ExportIn, a: ActorDep) -> ExportOut:
    with tx(a.org_id, a.id) as c:
        eid = exports.request(c, a, body.kind, body.start, body.end, body.reason)
        r = dict(c.execute(select(m.exports).where(m.exports.c.id == eid)).mappings().one())
    return _export_out(r, {a.id: a.display_name})


@router.get('/api/exports', response_model=list[ExportOut])
def list_exports(a: ActorDep) -> list[ExportOut]:
    a.require('export.request')
    with read(a.org_id, a.id) as c:
        q = select(m.exports).order_by(m.exports.c.created_at.desc()).limit(100)
        if a.rank < DIRECTOR:
            q = q.where(m.exports.c.requested_by == a.id)
        rows = [dict(r) for r in c.execute(q).mappings()]
        ids = {r['requested_by'] for r in rows} | {r['approved_by'] for r in rows if r['approved_by']}
        names = dict(c.execute(select(m.users.c.id, m.users.c.display_name).where(m.users.c.id.in_(ids or {uuid.UUID(int=0)}))).all())
    return [_export_out(r, names) for r in rows]


@router.post('/api/exports/{export_id}/decision', response_model=ExportOut)
def decide_export(export_id: str, body: ExportDecisionIn, a: ActorDep) -> ExportOut:
    eid = parse_uuid(export_id)
    with tx(a.org_id, a.id) as c:
        exports.decide(c, a, eid, body.approve, body.note)
        r = dict(c.execute(select(m.exports).where(m.exports.c.id == eid)).mappings().one())
        names = dict(c.execute(select(m.users.c.id, m.users.c.display_name).where(m.users.c.id.in_([r['requested_by'], a.id]))).all())
    return _export_out(r, names)


@router.post('/api/exports/{export_id}/password', response_model=PasswordOut)
def export_password(export_id: str, a: ActorDep) -> PasswordOut:
    a.require('export.request')
    with tx(a.org_id, a.id) as c:
        return PasswordOut(password=exports.reveal_password(c, a, parse_uuid(export_id)))


@router.get('/api/exports/{export_id}/download')
def export_download(export_id: str, a: ActorDep) -> Response:
    a.require('export.request')
    with tx(a.org_id, a.id) as c:
        name, data = exports.download(c, a, parse_uuid(export_id))
    return Response(data, media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="{name}"', 'Cache-Control': 'no-store'})

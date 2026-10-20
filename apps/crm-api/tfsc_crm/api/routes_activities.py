"""My Work / Sheet View capture: activities, Refine, Refine & Save, pre-dial check (§10-§12)."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import Connection, and_, func, or_, select

from tfsc_refine import ENGINE_VERSION, refine_phone

from .. import crypto
from ..db import models as m
from ..db.session import read, tx
from ..domain import activities as svc
from ..domain import duplicates, file_import, scoring
from ..domain.anti_scraping import record_reads
from ..ids import uuid7
from ..pacific import business_date, utcnow
from ..security import ratelimit
from ..security.roles import SUPERVISOR, Actor, in_scope, owner_filter
from .deps import ActorDep, decode_cursor, encode_cursor, parse_uuid
from .schemas import (
    ActivityIn,
    ActivityOut,
    ActivityPage,
    ActivityPatch,
    ActivityRoutingOut,
    BatchIn,
    BatchOut,
    BatchRowResult,
    IssueOut,
    PrecheckOut,
    ReasonIn,
    RefinedRowOut,
    RefineIn,
    RefineOut,
    RevisionOut,
    SaveOut,
)

router = APIRouter(prefix='/api', tags=['activities'])


def activity_out(a: Actor, r: dict, owner_name: str | None = None, c: Connection | None = None) -> ActivityOut | ActivityRoutingOut:
    now = utcnow()
    committed = r['first_committed_at'] is not None
    locked = bool(r['edit_deadline'] and now >= r['edit_deadline'])
    state = 'deleted' if r['deleted_at'] else 'locked' if locked else 'committed' if committed else 'data_hunting'
    phone = crypto.decrypt(r['phone_display'])
    e164 = refine_phone(phone).e164 if phone else None
    is_owner = r['owner_id'] == a.id
    can_edit = not r['deleted_at'] and ((is_owner and not locked) or a.rank >= SUPERVISOR)
    base = dict(
        id=r['id'], owner_id=r['owner_id'], owner_name=owner_name, business_date=r['business_date'], entry_source=r['entry_source'],
        provider_name=r['provider_name'], prospect_kind=r['prospect_kind'], phone=phone, phone_e164=e164, extension=r['extension'],
        email=crypto.decrypt(r['email']), called=r['called'], connected=r['connected'], text_sent=r['text_sent'], email_sent=r['email_sent'],
        followup_needed=r['followup_needed'], next_followup_at=r['next_followup_at'], remarks=r['remarks'], state=state,
        stage=scoring.stage_label(r['score_points'], committed, r['called']) if not r['deleted_at'] else 'Deleted',
        points=r['score_points'], first_committed_at=r['first_committed_at'], edit_deadline=r['edit_deadline'],
        seconds_left=max(0, int((r['edit_deadline'] - now).total_seconds())) if r['edit_deadline'] and not locked else None,
        conflict=r['conflict'] if a.rank >= SUPERVISOR or r['conflict'] is None else 'review', can_edit=can_edit,
        created_at=r['created_at'], version=r['version'])
    if a.can('routing.view'):
        return ActivityRoutingOut(**base, domain_id=r['domain_id'], project_id=r['project_id'])
    return ActivityOut(**base)


def _issues(items) -> list[IssueOut]:
    return [IssueOut(**{k: i[k] for k in ('field', 'severity', 'code', 'message')}) for i in items]


@router.post('/activities', response_model=SaveOut)
def create_activity(body: ActivityIn, a: ActorDep) -> SaveOut:
    ratelimit.enforce(f'act:{a.id}', 600, 60, 'You are saving entries too quickly. Please slow down.')
    row = body.model_dump(exclude={'owner_id'})
    with tx(a.org_id, a.id) as c:
        res = svc.create(c, a, row, source='manual', owner_id=body.owner_id)
        out = activity_out(a, res.activity)
    return SaveOut(activity=out, duplicate_notice=res.duplicate.notice, duplicate_severity=res.duplicate.severity if a.rank >= SUPERVISOR else
                   (None if res.duplicate.severity in (None, 'info') else 'notice'), issues=_issues(res.issues))


@router.get('/activities', response_model=ActivityPage)
def list_activities(a: ActorDep, request: Request, owner_id: str | None = None, state: str | None = None, date_from: date | None = None,
                    date_to: date | None = None, q: str | None = None, cursor: str | None = None, limit: int = 100,
                    deleted: bool = False) -> ActivityPage:
    limit = max(1, min(limit, 200))
    t = m.activities
    today = business_date()
    date_to = date_to or today
    date_from = date_from or (date_to - timedelta(days=30))
    if (date_to - date_from).days > 400:
        raise HTTPException(400, 'Choose a date range of at most 400 days.')
    with read(a.org_id, a.id) as c:
        stmt = select(t, m.users.c.display_name.label('owner_name')).join(m.users, m.users.c.id == t.c.owner_id).where(
            t.c.org_id == a.org_id, t.c.business_date.between(date_from, date_to), owner_filter(a, t.c.owner_id))
        if owner_id:
            stmt = stmt.where(t.c.owner_id == parse_uuid(owner_id))
        stmt = stmt.where(t.c.deleted_at.is_not(None) if deleted and a.rank >= SUPERVISOR else t.c.deleted_at.is_(None))
        if state == 'data_hunting':
            stmt = stmt.where(t.c.first_committed_at.is_(None))
        elif state == 'committed':
            stmt = stmt.where(t.c.first_committed_at.is_not(None))
        elif state == 'conflict' and a.rank >= SUPERVISOR:
            stmt = stmt.where(t.c.conflict.is_not(None))
        if q:
            stmt = stmt.where(t.c.provider_name.ilike(f'%{q.strip()[:80]}%'))
        cur = decode_cursor(cursor)
        if cur:
            ts, cid = datetime.fromisoformat(cur[0]), uuid.UUID(cur[1])
            stmt = stmt.where(or_(t.c.created_at < ts, and_(t.c.created_at == ts, t.c.id < cid)))
        rows = [dict(r) for r in c.execute(stmt.order_by(t.c.created_at.desc(), t.c.id.desc()).limit(limit + 1)).mappings()]
    items = [activity_out(a, r, r['owner_name']) for r in rows[:limit]]
    others = {r['phone_key'] for r in rows[:limit] if r['owner_id'] != a.id and r['phone_key']}
    if others:
        record_reads(a, 'activities', others)
    nxt = encode_cursor(rows[limit - 1]['created_at'], rows[limit - 1]['id']) if len(rows) > limit else None
    return ActivityPage(items=items, next_cursor=nxt)


def _load(c: Connection, a: Actor, activity_id: uuid.UUID) -> dict:
    r = c.execute(select(m.activities, m.users.c.display_name.label('owner_name')).join(m.users, m.users.c.id == m.activities.c.owner_id)
                  .where(m.activities.c.id == activity_id, m.activities.c.org_id == a.org_id)).mappings().first()
    if not r or not (r['owner_id'] == a.id or (a.rank >= SUPERVISOR and in_scope(c, a, r['owner_id']))):
        raise HTTPException(404, 'Record not found.')
    return dict(r)


@router.get('/activities/{activity_id}', response_model=ActivityRoutingOut | ActivityOut)
def get_activity(activity_id: str, a: ActorDep):
    with read(a.org_id, a.id) as c:
        r = _load(c, a, parse_uuid(activity_id))
    return activity_out(a, r, r['owner_name'])


@router.patch('/activities/{activity_id}', response_model=SaveOut)
def edit_activity(activity_id: str, body: ActivityPatch, a: ActorDep) -> SaveOut:
    patch = body.model_dump(exclude={'version', 'reason'}, exclude_unset=True)
    with tx(a.org_id, a.id) as c:
        res = svc.edit(c, a, parse_uuid(activity_id), patch, body.version, body.reason)
        out = activity_out(a, res.activity)
    return SaveOut(activity=out, duplicate_notice=res.duplicate.notice, duplicate_severity=res.duplicate.severity if a.rank >= SUPERVISOR else None,
                   issues=_issues(res.issues))


@router.post('/activities/{activity_id}/delete', response_model=ActivityRoutingOut | ActivityOut)
def delete_activity(activity_id: str, body: ReasonIn, a: ActorDep):
    with tx(a.org_id, a.id) as c:
        r = svc.soft_delete(c, a, parse_uuid(activity_id), body.reason)
        return activity_out(a, r)


@router.post('/activities/{activity_id}/restore', response_model=ActivityRoutingOut | ActivityOut)
def restore_activity(activity_id: str, body: ReasonIn, a: ActorDep):
    with tx(a.org_id, a.id) as c:
        r = svc.soft_delete(c, a, parse_uuid(activity_id), body.reason, restore=True)
        return activity_out(a, r)


@router.get('/activities/{activity_id}/revisions', response_model=list[RevisionOut])
def revisions(activity_id: str, a: ActorDep) -> list[RevisionOut]:
    with read(a.org_id, a.id) as c:
        r = _load(c, a, parse_uuid(activity_id))
        rows = c.execute(select(m.activity_revisions, m.users.c.display_name).join(m.users, m.users.c.id == m.activity_revisions.c.changed_by)
                         .where(m.activity_revisions.c.activity_id == r['id']).order_by(m.activity_revisions.c.rev)).mappings().all()
    return [RevisionOut(rev=x['rev'], changed_by=x['changed_by'], changed_by_name=x['display_name'], reason=x['reason'], before=x['before'],
                        after=x['after'], at=x['at']) for x in rows]


# ------------------------------------------------------------------ refine & batch
def _precheck_many(c: Connection, a: Actor, keys: list[str]) -> dict[str, str]:
    """One batched phone_registry lookup for all rows (§10.5)."""
    if not keys:
        return {}
    rows = c.execute(select(m.phone_registry).where(m.phone_registry.c.org_id == a.org_id, m.phone_registry.c.phone_key.in_(set(keys)))).mappings().all()
    return {r['phone_key']: duplicates.precheck_state(dict(r), a.id) for r in rows}


@router.post('/refine', response_model=RefineOut)
def refine(body: RefineIn, a: ActorDep) -> RefineOut:
    t0 = time.perf_counter()
    with read(a.org_id, a.id) as c:
        results = [svc.refine_input(row, c, a.org_id) for row in body.rows]
        keys = [crypto.phone_key(r['refined']['phone_e164']) for r in results]
        states = _precheck_many(c, a, [k for k in keys if k])
    rows, counts = [], {'green': 0, 'amber': 0, 'red': 0}
    for i, (r, k) in enumerate(zip(results, keys, strict=True)):
        counts[r['severity']] += 1
        rows.append(RefinedRowOut(row_no=i, refined=r['refined'], issues=_issues(r['issues']), severity=r['severity'],
                                  precheck=(states.get(k, 'new') if k else None)))
    return RefineOut(engine=ENGINE_VERSION, rows=rows, counts=counts, ms=round((time.perf_counter() - t0) * 1000, 1))


@router.post('/activities/batch', response_model=BatchOut)
def save_batch(body: BatchIn, a: ActorDep) -> BatchOut:
    """Refine & Save: refines authoritatively, saves every valid row in one transaction,
    returns invalid rows with reasons. Idempotent per (owner, request_key)."""
    t0 = time.perf_counter()
    ratelimit.enforce(f'batch:{a.id}', 60, 60, 'Too many batch saves. Please wait a moment.')
    body_hash = hashlib.sha256(json.dumps(body.rows, sort_keys=True, default=str).encode()).hexdigest()
    with tx(a.org_id, a.id) as c:
        prev = c.execute(select(m.import_batches).where(m.import_batches.c.owner_id == a.id, m.import_batches.c.request_key == body.request_key)).mappings().first()
        if prev:
            if prev['body_hash'] != body_hash:
                raise HTTPException(409, 'This request key was already used for different rows.')
            return BatchOut.model_validate({**prev['result'], 'replayed': True})
        batch_id = uuid7()
        c.execute(m.import_batches.insert().values(id=batch_id, org_id=a.org_id, owner_id=a.id, created_by=a.id, kind='paste' if body.source == 'grid' else 'file',
                                                   request_key=body.request_key, body_hash=body_hash, status='processing'))
        results: list[BatchRowResult] = []
        import_rows = []
        for i, row in enumerate(body.rows):
            r, rec = file_import.process_row(c, a, batch_id, i, row, body.source)
            results.append(BatchRowResult(row_no=i, status=r['status'], activity_id=r['activity_id'], issues=_issues(r['issues']),
                                          duplicate_notice=r.get('duplicate_notice')))
            import_rows.append(rec)
        if import_rows:
            c.execute(m.import_rows.insert(), import_rows)
        saved = sum(1 for r in results if r.status == 'saved')
        out = BatchOut(batch_id=batch_id, saved=saved, rejected=sum(1 for r in results if r.status == 'rejected'),
                       review=sum(1 for r in results if r.status == 'review'), rows=results, ms=round((time.perf_counter() - t0) * 1000, 1))
        c.execute(m.import_batches.update().where(m.import_batches.c.id == batch_id).values(
            status='done', totals={'saved': out.saved, 'rejected': out.rejected, 'review': out.review}, result=out.model_dump(mode='json')))
    return out


@router.get('/precheck', response_model=PrecheckOut)
def precheck(phone: str, a: ActorDep) -> PrecheckOut:
    """Pre-dial badge: returns a state only — never the other agent's identity or details (§11.1)."""
    ratelimit.enforce(f'precheck:{a.id}', 1200, 3600, 'Too many number checks. Please slow down.')
    p = refine_phone(phone)
    if not p.e164:
        return PrecheckOut(state='invalid', label='Check the number', phone=p.display or None)
    key = crypto.phone_key(p.e164)
    with read(a.org_id, a.id) as c:
        reg = c.execute(select(m.phone_registry).where(m.phone_registry.c.org_id == a.org_id, m.phone_registry.c.phone_key == key)).mappings().first()
        state = duplicates.precheck_state(dict(reg) if reg else None, a.id)
    if state != 'new':
        record_reads(a, 'precheck', {key or ''})
    return PrecheckOut(state=state, label=duplicates.PRECHECK_LABEL[state], phone=p.display)  # type: ignore[arg-type]


@router.get('/activities-summary/today')
def my_today(a: ActorDep) -> dict:
    """Small counters for the My Work header (own rows only)."""
    with read(a.org_id, a.id) as c:
        t = m.agent_day_stats
        r = c.execute(select(t).where(t.c.user_id == a.id, t.c.business_date == business_date())).mappings().first()
        locked_soon = c.execute(select(func.count()).select_from(m.activities).where(
            m.activities.c.owner_id == a.id, m.activities.c.business_date == business_date(), m.activities.c.edit_deadline > utcnow())).scalar_one()
    keys = ('attempts', 'connected', 'any_touch', 'research', 'points_event', 'points')
    return {**{k: (r[k] if r else 0) for k in keys}, 'editable_rows': locked_soon, 'business_date': business_date().isoformat()}

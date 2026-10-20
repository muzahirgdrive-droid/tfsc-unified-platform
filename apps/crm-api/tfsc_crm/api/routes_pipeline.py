"""Follow-ups, strong follow-ups, leads, flags and notifications (§12-§14)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import Connection, and_, func, or_, select, update

from .. import crypto
from ..db import models as m
from ..db.session import read, tx
from ..domain import audit, duplicates, pipeline, scoring
from ..domain import settings as cfg
from ..ids import uuid7
from ..pacific import PACIFIC, business_date, utcnow
from ..security.roles import SUPER, SUPERVISOR, Actor, group_member_ids, in_scope, owner_filter, scope_ids
from .deps import ActorDep, decode_cursor, encode_cursor, parse_uuid
from .schemas import (
    ConvertIn,
    FlagAck,
    FlagOut,
    FlagPage,
    FlagResolve,
    FollowupAction,
    FollowupOut,
    FollowupPage,
    IdsIn,
    LeadAgentOut,
    LeadOut,
    LeadPage,
    LeadTransitionIn,
    NotificationOut,
    NotificationPage,
    OkOut,
    ReassignIn,
    StrongComment,
    StrongDecision,
    StrongOut,
    StrongRequest,
)

router = APIRouter(prefix='/api', tags=['pipeline'])

STAGE_LABEL = {'qualified_lead': 'Lead', 'meeting': 'Meeting', 'signed_up': 'Signed Up', 'agreement_in_progress': 'Agreement in progress',
               'client': 'Client', 'lost': 'Lost'}


# ------------------------------------------------------------------ follow-ups
def _bucket(due: datetime | None, status: str) -> str:
    if status != 'open':
        return 'closed'
    if due is None:
        return 'backlog'
    today = business_date()
    d = due.astimezone(PACIFIC).date()
    if d > today:
        return 'upcoming'
    if d == today:
        return 'due_today'
    return 'overdue_3' if (today - d).days >= 3 else 'overdue_1'


@router.get('/followups', response_model=FollowupPage)
def list_followups(a: ActorDep, view: str = 'mine', status: str = 'open', limit: int = 300) -> FollowupPage:
    f, act = m.followups, m.activities
    limit = max(1, min(limit, 500))
    with read(a.org_id, a.id) as c:
        stmt = select(f, act.c.provider_name, act.c.phone_display, m.users.c.display_name.label('owner_name')) \
            .join(m.users, m.users.c.id == f.c.owner_id) \
            .outerjoin(act, and_(act.c.id == f.c.activity_id, act.c.business_date == f.c.activity_date)).where(f.c.org_id == a.org_id)
        if view == 'group':
            if not a.can('followups.group'):
                raise HTTPException(403, 'Only group leaders and supervisors see group follow-ups.')
            stmt = stmt.where(f.c.owner_id.in_(group_member_ids(a)) if a.rank < SUPERVISOR else f.c.owner_id.in_(scope_ids(a)))
        elif view == 'team':
            if a.rank < SUPERVISOR:
                raise HTTPException(403, 'You do not have permission for this action.')
            stmt = stmt.where(f.c.owner_id.in_(scope_ids(a)))
        else:
            stmt = stmt.where(f.c.owner_id == a.id)
        if status != 'all':
            stmt = stmt.where(f.c.status == status)
        rows = c.execute(stmt.order_by(f.c.due_at.asc().nulls_last(), f.c.created_at).limit(limit)).mappings().all()
        notes: dict[uuid.UUID, list] = {}
        if rows:
            for n in c.execute(select(m.followup_notes, m.users.c.display_name).join(m.users, m.users.c.id == m.followup_notes.c.author_id)
                               .where(m.followup_notes.c.followup_id.in_([r['id'] for r in rows])).order_by(m.followup_notes.c.created_at)).mappings():
                notes.setdefault(n['followup_id'], []).append({'author': n['display_name'], 'body': n['body'], 'at': n['created_at'].isoformat()})
    items, counts = [], {'backlog': 0, 'upcoming': 0, 'due_today': 0, 'overdue_1': 0, 'overdue_3': 0, 'closed': 0}
    for r in rows:
        b = _bucket(r['due_at'], r['status'])
        counts[b] += 1
        items.append(FollowupOut(id=r['id'], owner_id=r['owner_id'], owner_name=r['owner_name'], activity_id=r['activity_id'], provider_name=r['provider_name'],
                                 phone=crypto.decrypt(r['phone_display']), due_at=r['due_at'], status=r['status'], bucket=b,  # type: ignore[arg-type]
                                 outcome_note=r['outcome_note'], notes=notes.get(r['id'], []), version=r['version']))
    return FollowupPage(items=items, counts=counts)


@router.post('/followups/{followup_id}/action', response_model=OkOut)
def followup_action(followup_id: str, body: FollowupAction, a: ActorDep) -> OkOut:
    with tx(a.org_id, a.id) as c:
        pipeline.followup_action(c, a, parse_uuid(followup_id), body.action, body.note, body.due_at, body.version)
    return OkOut()


# ------------------------------------------------------------------ strong follow-ups
@router.post('/activities/{activity_id}/strong', response_model=dict)
def request_strong(activity_id: str, body: StrongRequest, a: ActorDep) -> dict:
    with tx(a.org_id, a.id) as c:
        r = pipeline.request_strong(c, a, parse_uuid(activity_id), body.target_window, body.note)
    return {'id': str(r['id']), 'decision': r['decision']}


def _strong_out(c: Connection, a: Actor, r: dict, with_comments: bool = True) -> StrongOut:
    comments = []
    if with_comments:
        comments = [{'author': x['display_name'], 'body': x['body'], 'at': x['created_at'].isoformat()} for x in c.execute(
            select(m.strong_followup_comments, m.users.c.display_name).join(m.users, m.users.c.id == m.strong_followup_comments.c.author_id)
            .where(m.strong_followup_comments.c.strong_followup_id == r['id']).order_by(m.strong_followup_comments.c.created_at)).mappings()]
    sug, rule = (None, None)
    if a.rank >= SUPERVISOR and r['decision'] == 'confirmed':
        sug, rule = pipeline.default_owner(c, a, r)
    return StrongOut(id=r['id'], activity_id=r['activity_id'], provider_name=r.get('provider_name'), requested_by=r['requested_by'],
                     requested_by_name=r.get('requested_by_name'), requested_at=r['requested_at'], decision=r['decision'], decided_at=r['decided_at'],
                     reason=r['reason'], target_window=r['target_window'], note=r['note'], last_remarks=r.get('remarks'), comments=comments,
                     suggested_owner_id=sug, suggested_owner_rule=rule, version=r['version'])


@router.get('/strong', response_model=list[StrongOut])
def list_strong(a: ActorDep, decision: str = 'pending') -> list[StrongOut]:
    s, act = m.strong_followups, m.activities
    with read(a.org_id, a.id) as c:
        stmt = select(s, act.c.provider_name, act.c.remarks, m.users.c.display_name.label('requested_by_name')) \
            .join(act, and_(act.c.id == s.c.activity_id, act.c.business_date == s.c.activity_date)) \
            .join(m.users, m.users.c.id == s.c.requested_by).where(s.c.org_id == a.org_id, owner_filter(a, s.c.requested_by))
        if decision != 'all':
            stmt = stmt.where(s.c.decision == decision)
        rows = c.execute(stmt.order_by(s.c.requested_at).limit(500)).mappings().all()
        return [_strong_out(c, a, dict(r)) for r in rows]


@router.post('/strong/{sid}/decide', response_model=dict)
def decide_strong(sid: str, body: StrongDecision, a: ActorDep) -> dict:
    with tx(a.org_id, a.id) as c:
        r = pipeline.decide_strong(c, a, parse_uuid(sid), body.decision, body.reason, body.version)
    return {'id': str(r['id']), 'decision': r['decision']}


@router.post('/strong/{sid}/comments', response_model=OkOut)
def comment_strong(sid: str, body: StrongComment, a: ActorDep) -> OkOut:
    with tx(a.org_id, a.id) as c:
        s = c.execute(select(m.strong_followups).where(m.strong_followups.c.id == parse_uuid(sid))).mappings().first()
        if not s or not (s['requested_by'] == a.id or (a.rank >= SUPERVISOR and in_scope(c, a, s['requested_by']))):
            raise HTTPException(404, 'Strong follow-up not found.')
        c.execute(m.strong_followup_comments.insert().values(id=uuid7(), org_id=a.org_id, strong_followup_id=s['id'], author_id=a.id, body=body.body))
        from ..domain import notify
        target = s['requested_by'] if a.id != s['requested_by'] else s['supervisor_id']
        notify.notify(c, a.org_id, [target], 'strong.comment', {'strong_followup_id': str(s['id']), 'from': a.display_name})
    return OkOut()


@router.post('/strong/{sid}/convert', response_model=dict)
def convert(sid: str, body: ConvertIn, a: ActorDep) -> dict:
    with tx(a.org_id, a.id) as c:
        r = pipeline.convert_to_lead(c, a, parse_uuid(sid), body.owner_id, body.reason)
    return {k: str(v) for k, v in r.items()}


# ------------------------------------------------------------------ leads
def _lead_rows(c: Connection, a: Actor, where) -> list[dict]:
    ld, act = m.leads, m.activities
    stmt = select(ld, act.c.provider_name, act.c.phone_display, act.c.next_followup_at, m.users.c.display_name.label('owner_name')) \
        .join(act, and_(act.c.id == ld.c.origin_activity_id, act.c.business_date == ld.c.origin_activity_date)) \
        .join(m.users, m.users.c.id == ld.c.owner_id).where(ld.c.org_id == a.org_id, owner_filter(a, ld.c.owner_id), where)
    return [dict(r) for r in c.execute(stmt.order_by(ld.c.stage_changed_at.desc(), ld.c.id.desc()).limit(201)).mappings()]


def lead_out(c: Connection, a: Actor, r: dict, detail: bool = False) -> LeadOut | LeadAgentOut:
    stage_label = STAGE_LABEL.get(r['stage'], r['stage'])
    base = dict(id=r['id'], provider_name=r['provider_name'], phone=crypto.decrypt(r['phone_display']), stage=r['stage'], owner_id=r['owner_id'],
                next_followup_at=r['next_followup_at'], created_at=r['created_at'], version=r['version'])
    if a.rank < SUPERVISOR:
        if r['stage'] == 'meeting':  # §14.3: agents never learn meeting details, not even the stage name
            stage_label = cfg.get(c, a.org_id, 'agent_meeting_label') or 'With management'
            base['stage'] = 'with_management'
        return LeadAgentOut(**base, stage_label=stage_label)
    project_owner = None
    if r['project_id'] and a.can('routing.view'):
        project_owner = c.execute(select(m.projects.c.owner_name).where(m.projects.c.id == r['project_id'])).scalar_one_or_none()
    out = LeadOut(**base, stage_label=stage_label, owner_name=r.get('owner_name'), supervisor_id=r['supervisor_id'], qualified_by=r['qualified_by'],
                  qualified_at=r['qualified_at'], signed_at=r['signed_at'], lost_reason=r['lost_reason'], agreement_status=r['agreement_status'],
                  agreement_locked=r['agreement_locked_at'] is not None,
                  domain_id=r['domain_id'] if a.can('routing.view') else None, project_id=r['project_id'] if a.can('routing.view') else None,
                  project_owner=project_owner)
    if detail:
        out.attribution = [{'user_id': str(x['user_id']), 'user': x['display_name'], 'role': x['role'], 'share': float(x['share']), 'at': x['at'].isoformat()}
                           for x in c.execute(select(m.attribution_ledger, m.users.c.display_name).join(m.users, m.users.c.id == m.attribution_ledger.c.user_id)
                                              .where(m.attribution_ledger.c.lead_id == r['id']).order_by(m.attribution_ledger.c.at)).mappings()]
        out.meetings = [{'id': str(x['id']), 'title': x['title'], 'starts_at': x['starts_at'].isoformat(), 'status': x['status']}
                        for x in c.execute(select(m.meetings).where(m.meetings.c.lead_id == r['id']).order_by(m.meetings.c.starts_at)).mappings()]
        out.timeline = timeline(c, a, r)
    return out


def timeline(c: Connection, a: Actor, r: dict) -> list[dict]:
    """Every activity by everyone on this phone + strong FU, assignments, flags (Supervisor+ only)."""
    ev = []
    if r['phone_key']:
        for x in c.execute(select(m.activities, m.users.c.display_name).join(m.users, m.users.c.id == m.activities.c.owner_id)
                           .where(m.activities.c.org_id == a.org_id, m.activities.c.phone_key == r['phone_key'])
                           .order_by(m.activities.c.created_at).limit(300)).mappings():
            ev.append({'at': x['created_at'].isoformat(), 'kind': 'activity', 'by': x['display_name'], 'stage': scoring.stage_label(x['score_points'], x['first_committed_at'] is not None, x['called']),
                       'remarks': x['remarks'][:500], 'deleted': x['deleted_at'] is not None})
        for x in c.execute(select(m.flags).where(m.flags.c.org_id == a.org_id, m.flags.c.phone_key == r['phone_key'])).mappings():
            ev.append({'at': x['created_at'].isoformat(), 'kind': 'flag', 'severity': x['severity'], 'status': x['status']})
    for x in c.execute(select(m.lead_assignments, m.users.c.display_name).join(m.users, m.users.c.id == m.lead_assignments.c.to_user)
                       .where(m.lead_assignments.c.lead_id == r['id'])).mappings():
        ev.append({'at': x['at'].isoformat(), 'kind': 'assignment', 'to': x['display_name'], 'reason': x['reason']})
    for x in c.execute(select(m.audit_log.c.action, m.audit_log.c.at, m.audit_log.c.after).where(m.audit_log.c.resource == 'lead', m.audit_log.c.resource_id == str(r['id']))).mappings():
        ev.append({'at': x['at'].isoformat(), 'kind': 'transition', 'action': x['action'], 'detail': x['after']})
    return sorted(ev, key=lambda e: e['at'])


@router.get('/leads', response_model=LeadPage)
def list_leads(a: ActorDep, stage: str | None = None, cursor: str | None = None) -> LeadPage:
    with read(a.org_id, a.id) as c:
        cond = m.leads.c.stage == stage if stage else m.leads.c.stage != 'lost'
        cur = decode_cursor(cursor)
        if cur:
            cond = and_(cond, or_(m.leads.c.stage_changed_at < datetime.fromisoformat(cur[0]),
                                  and_(m.leads.c.stage_changed_at == datetime.fromisoformat(cur[0]), m.leads.c.id < uuid.UUID(cur[1]))))
        rows = _lead_rows(c, a, cond)
        items = [lead_out(c, a, r) for r in rows[:200]]
    nxt = encode_cursor(rows[199]['stage_changed_at'], rows[199]['id']) if len(rows) > 200 else None
    return LeadPage(items=items, next_cursor=nxt)


@router.get('/leads/{lead_id}', response_model=LeadOut | LeadAgentOut)
def get_lead(lead_id: str, a: ActorDep):
    with read(a.org_id, a.id) as c:
        rows = _lead_rows(c, a, m.leads.c.id == parse_uuid(lead_id))
        if not rows:
            raise HTTPException(404, 'Lead not found.')
        return lead_out(c, a, rows[0], detail=True)


@router.post('/leads/{lead_id}/transition', response_model=LeadOut | LeadAgentOut)
def lead_transition(lead_id: str, body: LeadTransitionIn, a: ActorDep):
    lid = parse_uuid(lead_id)
    with tx(a.org_id, a.id) as c:
        pipeline.transition(c, a, lid, body.action, body.reason, body.version)
        return lead_out(c, a, _lead_rows(c, a, m.leads.c.id == lid)[0])


@router.post('/leads/{lead_id}/reassign', response_model=LeadOut | LeadAgentOut)
def lead_reassign(lead_id: str, body: ReassignIn, a: ActorDep):
    lid = parse_uuid(lead_id)
    with tx(a.org_id, a.id) as c:
        pipeline.reassign(c, a, lid, body.to_user, body.reason, body.incentive_split, body.version)
        return lead_out(c, a, _lead_rows(c, a, m.leads.c.id == lid)[0])


# ------------------------------------------------------------------ flags
def _flag_scope(a: Actor):
    f = m.flags
    if a.rank >= SUPER:
        return f.c.org_id == a.org_id
    ids = scope_ids(a)
    return or_(f.c.raised_for_user.in_(ids), f.c.other_user.in_(ids), f.c.assigned_to_supervisor == a.id)


@router.get('/flags', response_model=FlagPage)
def list_flags(a: ActorDep, status: str = 'open', severity: str | None = None, kind: str | None = None, max_age_hours: int | None = None) -> FlagPage:
    a.require('flags.view')
    f = m.flags
    u1, u2 = m.users.alias('u1'), m.users.alias('u2')
    with read(a.org_id, a.id) as c:
        stmt = select(f, u1.c.display_name.label('raised_name'), u2.c.display_name.label('other_name'), m.activities.c.provider_name) \
            .outerjoin(u1, u1.c.id == f.c.raised_for_user).outerjoin(u2, u2.c.id == f.c.other_user) \
            .outerjoin(m.activities, m.activities.c.id == f.c.activity_id).where(f.c.org_id == a.org_id, _flag_scope(a))
        if status != 'all':
            stmt = stmt.where(f.c.status == status)
        if severity:
            stmt = stmt.where(f.c.severity == severity)
        if kind:
            stmt = stmt.where(f.c.kind == kind)
        if max_age_hours:
            stmt = stmt.where(f.c.created_at > utcnow() - timedelta(hours=max_age_hours))
        rows = c.execute(stmt.order_by(f.c.severity.desc(), f.c.created_at.desc()).limit(500)).mappings().all()
        counts: dict[str, int] = dict(c.execute(select(f.c.severity, func.count()).where(f.c.org_id == a.org_id, _flag_scope(a), f.c.status == 'open').group_by(f.c.severity)).all())
    now = utcnow()
    return FlagPage(items=[FlagOut(id=r['id'], severity=r['severity'], kind=r['kind'], status=r['status'], created_at=r['created_at'], activity_id=r['activity_id'],
                                   other_activity_id=r['other_activity_id'], lead_id=r['lead_id'], raised_for_user=r['raised_for_user'], raised_for_name=r['raised_name'],
                                   other_user=r['other_user'], other_user_name=r['other_name'], provider_name=r['provider_name'], resolution=r['resolution'],
                                   resolution_note=r['resolution_note'], age_hours=round((now - r['created_at']).total_seconds() / 3600, 1), version=r['version'])
                           for r in rows], counts={k: int(v) for k, v in counts.items()})


@router.post('/flags/{flag_id}/resolve', response_model=OkOut)
def resolve_flag(flag_id: str, body: FlagResolve, a: ActorDep) -> OkOut:
    a.require('flags.view')
    with tx(a.org_id, a.id) as c:
        f = c.execute(select(m.flags).where(m.flags.c.id == parse_uuid(flag_id), m.flags.c.org_id == a.org_id, _flag_scope(a)).with_for_update()).mappings().first()
        if not f:
            raise HTTPException(404, 'Flag not found.')
        if f['version'] != body.version:
            raise HTTPException(409, 'This flag changed. Refresh first.')
        if f['status'] == 'resolved':
            raise HTTPException(409, 'Already resolved.')
        act = None
        if f['activity_id']:
            act = c.execute(select(m.activities).where(m.activities.c.id == f['activity_id']).with_for_update()).mappings().first()
        if body.resolution == 'transfer':
            if f['lead_id']:
                lead = c.execute(select(m.leads.c.version).where(m.leads.c.id == f['lead_id'])).scalar_one()
                if not body.incentive_split:
                    raise HTTPException(400, 'Record the incentive split for the transfer.')
                pipeline.reassign(c, a, f['lead_id'], f['raised_for_user'], body.note, body.incentive_split, lead)
            elif f['phone_key']:
                c.execute(update(m.followups).where(m.followups.c.org_id == a.org_id, m.followups.c.phone_key == f['phone_key'], m.followups.c.status == 'open')
                          .values(owner_id=f['raised_for_user']))
                duplicates.set_state(c, a.org_id, f['phone_key'], 'followup', f['raised_for_user'], only_upgrade=False)
        if body.resolution == 'shared_line' and f['phone_key']:
            c.execute(update(m.contact_points).where(m.contact_points.c.org_id == a.org_id, m.contact_points.c.value_key == f['phone_key']).values(is_shared_line=True))
        if body.resolution in ('transfer', 'shared_line', 'data_error') and act and act['conflict']:
            c.execute(update(m.activities).where(m.activities.c.id == act['id'], m.activities.c.business_date == act['business_date']).values(conflict=None))
            scoring.rescore(c, a.org_id, act['id'], act['business_date'])
        status = 'ack' if body.resolution == 'acknowledged' else 'resolved'
        if status == 'ack' and f['severity'] == 'red':
            raise HTTPException(400, 'Red alerts must be resolved, not just acknowledged.')
        c.execute(update(m.flags).where(m.flags.c.id == f['id']).values(status=status, resolution=body.resolution, resolution_note=body.note,
                                                                        resolved_by=a.id, resolved_at=utcnow(), version=f['version'] + 1))
        audit.record(c, a.org_id, a.id, f'flag.{status}', 'flag', f['id'], {'status': f['status']}, {'resolution': body.resolution, 'note': body.note})
    return OkOut()


@router.post('/flags/ack', response_model=OkOut)
def ack_flags(body: FlagAck, a: ActorDep) -> OkOut:
    """Bulk acknowledge (amber only)."""
    a.require('flags.view')
    with tx(a.org_id, a.id) as c:
        res = c.execute(update(m.flags).where(m.flags.c.id.in_(body.ids), m.flags.c.org_id == a.org_id, m.flags.c.severity.in_(['amber', 'info']),
                                              m.flags.c.status == 'open', _flag_scope(a)).values(status='ack', resolved_by=a.id, resolved_at=utcnow(),
                                                                                                 version=m.flags.c.version + 1))
        audit.record(c, a.org_id, a.id, 'flag.bulk_ack', 'flag', None, None, {'count': res.rowcount})
    return OkOut()


# ------------------------------------------------------------------ notifications
@router.get('/notifications', response_model=NotificationPage)
def notifications(a: ActorDep, unread_only: bool = False) -> NotificationPage:
    n = m.notifications
    with read(a.org_id, a.id) as c:
        stmt = select(n).where(n.c.user_id == a.id)
        if unread_only:
            stmt = stmt.where(n.c.read_at.is_(None))
        rows = c.execute(stmt.order_by(n.c.created_at.desc()).limit(100)).mappings().all()
        unread = c.execute(select(func.count()).select_from(n).where(n.c.user_id == a.id, n.c.read_at.is_(None))).scalar_one()
    return NotificationPage(items=[NotificationOut(id=r['id'], kind=r['kind'], payload=r['payload'], read_at=r['read_at'], created_at=r['created_at']) for r in rows],
                            unread=unread)


@router.post('/notifications/read', response_model=OkOut)
def mark_read(body: IdsIn, a: ActorDep) -> OkOut:
    with tx(a.org_id, a.id) as c:
        stmt = update(m.notifications).where(m.notifications.c.user_id == a.id, m.notifications.c.read_at.is_(None))
        if body.ids:
            stmt = stmt.where(m.notifications.c.id.in_(body.ids))
        c.execute(stmt.values(read_at=utcnow()))
    return OkOut()

"""§27 lead protection: an open lead (qualified or in a meeting) with no activity for the protection window (setting
`lead_protection_days`, default 90) prompts its supervisor, once, to release it or renew its protection. Nothing is
released automatically, and a lead with a scheduled meeting is never released: it is not prompted, and a release is
refused until the meeting is cancelled.

Activity = the lead's own changes (creation, stage, reassignment, renewal, meetings) or any activity on its number by
anyone. A release closes the lead as lost with the reason and frees the number for every agent (reopen still works).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import ColumnElement, Connection, Exists, exists, func, select, update

from ..db import models as m
from ..ids import uuid7
from ..pacific import utcnow
from ..security.roles import SUPERVISOR, Actor, in_scope
from . import audit, notify, pipeline
from . import settings as cfg

OPEN_STAGES = ('qualified_lead', 'meeting')
FLAG_KIND = 'lead_protection_review'


def _last_activity() -> ColumnElement[datetime]:
    ld, act, la, mt = m.leads, m.activities, m.lead_assignments, m.meetings
    # GREATEST ignores NULLs in PostgreSQL
    return func.greatest(
        ld.c.created_at, ld.c.stage_changed_at, ld.c.protection_renewed_at,
        select(func.max(act.c.updated_at)).where(act.c.org_id == ld.c.org_id, act.c.phone_key == ld.c.phone_key).scalar_subquery(),
        select(func.max(la.c.at)).where(la.c.lead_id == ld.c.id).scalar_subquery(),
        select(func.max(mt.c.updated_at)).where(mt.c.lead_id == ld.c.id).scalar_subquery())


def _scheduled_meeting() -> Exists:
    return exists().where(m.meetings.c.lead_id == m.leads.c.id, m.meetings.c.status == 'scheduled')


def status(c: Connection, org_id: uuid.UUID, lead_id: uuid.UUID) -> dict:
    """For the supervisor's lead view."""
    days = int(cfg.get(c, org_id, 'lead_protection_days') or 90)
    last, review = c.execute(select(_last_activity(), m.leads.c.protection_review_at).where(m.leads.c.id == lead_id)).one()
    return {'window_days': days, 'last_activity_at': last.isoformat(), 'days_inactive': (utcnow() - last).days,
            'review_pending': review is not None}


def prompt_due(c: Connection, org_id: uuid.UUID, now: datetime) -> int:
    """Scheduler job: raise one review prompt per lead that crossed the window (idempotent via protection_review_at)."""
    days = int(cfg.get(c, org_id, 'lead_protection_days') or 90)
    ld = m.leads
    last = _last_activity().label('last_at')
    inner = select(ld.c.id, ld.c.owner_id, ld.c.supervisor_id, ld.c.phone_key, ld.c.origin_activity_id, last).where(
        ld.c.org_id == org_id, ld.c.stage.in_(OPEN_STAGES), ld.c.protection_review_at.is_(None), ~_scheduled_meeting()).subquery()
    due = c.execute(select(inner).where(inner.c.last_at < now - timedelta(days=days))).mappings().all()
    for r in due:
        n = (now - r['last_at']).days
        sup = r['supervisor_id'] or next(iter(notify.supervisors_of(c, r['owner_id'])), None)
        message = f'No activity on this lead for {n} days. Release it to the pool or renew its protection.'
        c.execute(m.flags.insert().values(id=uuid7(), org_id=org_id, severity='amber', kind=FLAG_KIND, phone_key=r['phone_key'], lead_id=r['id'],
                                          activity_id=r['origin_activity_id'], raised_for_user=r['owner_id'], assigned_to_supervisor=sup,
                                          detail={'days_inactive': n, 'last_activity_at': r['last_at'].isoformat(), 'message': message}))
        c.execute(update(ld).where(ld.c.id == r['id']).values(protection_review_at=now))
        if sup:
            notify.notify(c, org_id, [sup], 'lead.protection_review', {'lead_id': str(r['id']), 'days_inactive': n, 'message': message})
    return len(due)


def decide(c: Connection, actor: Actor, lead_id: uuid.UUID, action: str, reason: str | None, version: int) -> None:
    if actor.rank < SUPERVISOR:
        raise HTTPException(403, 'Only a supervisor can release a lead or renew its protection.')
    if action not in ('renew', 'release'):
        raise HTTPException(400, 'Choose renew or release.')
    ld = c.execute(select(m.leads).where(m.leads.c.id == lead_id, m.leads.c.org_id == actor.org_id).with_for_update()).mappings().first()
    if not ld or not in_scope(c, actor, ld['owner_id']):
        raise HTTPException(404, 'Lead not found.')
    if ld['version'] != version:
        raise HTTPException(409, 'This lead changed. Refresh first.')
    if ld['stage'] not in OPEN_STAGES:
        raise HTTPException(409, 'Only an open lead (qualified or in a meeting) can be released or renewed.')
    note = (reason or '').strip()
    now = utcnow()
    if action == 'renew':
        c.execute(update(m.leads).where(m.leads.c.id == lead_id).values(protection_renewed_at=now, protection_review_at=None,
                                                                          version=ld['version'] + 1, updated_at=now))
    else:
        if not note:
            raise HTTPException(400, 'A reason is required to release a lead.')
        if c.execute(select(m.meetings.c.id).where(m.meetings.c.lead_id == lead_id, m.meetings.c.status == 'scheduled')).first():
            raise HTTPException(409, 'This lead has a scheduled meeting and is never released. Cancel the meeting first.')
        pipeline.transition(c, actor, lead_id, 'lost', f'Released after protection review: {note}', version)
        notify.notify(c, actor.org_id, [ld['owner_id']], 'lead.released', {'lead_id': str(lead_id), 'message': 'A supervisor released this lead to the pool.'})
    c.execute(update(m.flags).where(m.flags.c.lead_id == lead_id, m.flags.c.kind == FLAG_KIND, m.flags.c.status != 'resolved').values(
        status='resolved', resolution='renewed' if action == 'renew' else 'released', resolution_note=note or None, resolved_by=actor.id,
        resolved_at=now, updated_at=now))
    audit.record(c, actor.org_id, actor.id, 'lead.protection_renewed' if action == 'renew' else 'lead.protection_released', 'lead', lead_id,
                 {'protection_review_at': ld['protection_review_at'].isoformat() if ld['protection_review_at'] else None}, {'reason': note or None})

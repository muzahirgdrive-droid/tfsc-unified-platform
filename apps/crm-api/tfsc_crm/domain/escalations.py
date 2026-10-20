"""Time-based escalations run by the scheduler (§12, §13).

- Red flags unresolved for 24 h escalate to the managers above the assigned supervisor (and Super Admins).
- Follow-ups overdue 3+ days notify the owner's supervisor once.
Both are idempotent: a row is escalated once (escalated_at), so re-running a job never notifies twice.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import Connection, select, update

from ..db import models as m
from . import notify

RED_FLAG_SLA = timedelta(hours=24)
FOLLOWUP_RED = timedelta(days=3)


def flag_sla(c: Connection, org_id: uuid.UUID, now: datetime) -> int:
    f = m.flags
    due = c.execute(select(f.c.id, f.c.kind, f.c.assigned_to_supervisor, f.c.raised_for_user).where(
        f.c.org_id == org_id, f.c.severity == 'red', f.c.status != 'resolved', f.c.escalated_at.is_(None),
        f.c.created_at <= now - RED_FLAG_SLA).with_for_update(skip_locked=True)).all()
    for fid, kind, sup, raised_for in due:
        anchor_user = sup or raised_for
        managers = [u for u in notify.supervisors_of(c, anchor_user, 3) if u != sup] if anchor_user else []
        recipients = set(managers[:1]) | set(notify.super_admins(c, org_id))  # the Manager above the supervisor (§12)
        notify.notify(c, org_id, recipients, 'flag.escalated', {'flag_id': str(fid), 'kind': kind,
                                                                 'message': 'A red flag has been open for more than 24 hours.'})
        c.execute(update(f).where(f.c.id == fid).values(escalated_at=now))
    return len(due)


def followups_overdue(c: Connection, org_id: uuid.UUID, now: datetime) -> int:
    t = m.followups
    due = c.execute(select(t.c.id, t.c.owner_id, t.c.due_at).where(
        t.c.org_id == org_id, t.c.status == 'open', t.c.escalated_at.is_(None), t.c.due_at.is_not(None),
        t.c.due_at <= now - FOLLOWUP_RED).with_for_update(skip_locked=True)).all()
    by_owner: dict[uuid.UUID, list[str]] = {}
    for fid, owner, _due in due:
        by_owner.setdefault(owner, []).append(str(fid))
    for owner, ids in by_owner.items():
        name = c.execute(select(m.users.c.display_name).where(m.users.c.id == owner)).scalar_one_or_none() or 'An agent'
        notify.notify(c, org_id, notify.supervisors_of(c, owner, 1), 'followup.overdue',
                      {'agent': name, 'count': len(ids), 'followup_ids': ids[:50],
                       'message': f'{name} has {len(ids)} follow-up(s) overdue by 3+ days.'})
    if due:
        c.execute(update(t).where(t.c.id.in_([d[0] for d in due])).values(escalated_at=now))
    return len(due)

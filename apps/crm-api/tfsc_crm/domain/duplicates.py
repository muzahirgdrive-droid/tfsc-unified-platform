"""Duplicate and conflict engine (§12) over the O(1) phone_registry (ADR-0005).

Called inside the save transaction. The registry row is locked with FOR UPDATE so
concurrent saves of the same number serialize; first-contact facts are therefore
decided by server commit order, never by row order or typed dates (§11.3).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert

from ..db import models as m
from ..ids import uuid7
from . import notify

PROTECTED = {'lead', 'meeting', 'signed', 'client'}
FOLLOWUP = {'followup'}
STRONG = {'strong_pending', 'strong_confirmed'}

AGENT_NOTICE = {
    None: 'New',
    'seen': 'Seen by team',
    'followup': 'Already in follow-up — supervisor notified',
    'strong': 'Already in follow-up — supervisor notified',
    'protected': 'Protected lead — do not pursue. Your entry was recorded and sent to your supervisor.',
}
PRECHECK_LABEL = {'new': 'New', 'seen': 'Seen by team', 'followup': 'In follow-up', 'strong': 'Strong follow-up', 'protected': 'Protected lead – do not pursue'}


@dataclass
class DupResult:
    severity: str | None  # None | info | amber | red
    notice: str
    flag_id: uuid.UUID | None = None


def _other_owner(reg: dict) -> uuid.UUID | None:
    return reg.get('protected_owner_id') or reg.get('state_owner_id')


def precheck_state(reg: dict | None, owner_id: uuid.UUID) -> str:
    """State label for the pre-dial badge. Never reveals who owns the number."""
    if not reg:
        return 'new'
    if reg['state'] in PROTECTED:
        return 'protected' if reg.get('protected_owner_id') != owner_id else 'followup'
    if reg['state'] in STRONG:
        return 'strong'
    if reg['state'] in FOLLOWUP:
        return 'followup'
    return 'seen' if reg.get('first_seen_by') and reg.get('first_seen_by') != owner_id else 'new'


def lock_registry(c: Connection, org_id: uuid.UUID, phone_key: str) -> dict:
    c.execute(insert(m.phone_registry).values(org_id=org_id, phone_key=phone_key, state='none').on_conflict_do_nothing())
    return dict(c.execute(select(m.phone_registry).where(m.phone_registry.c.org_id == org_id, m.phone_registry.c.phone_key == phone_key)
                          .with_for_update()).mappings().one())


def record_contact(c: Connection, org_id: uuid.UUID, reg: dict, activity: dict, at: datetime) -> None:
    """Update first-seen / first-attempt / first-connect facts (only if not set yet)."""
    vals: dict = {'last_activity_at': at, 'updated_at': at}
    if not reg.get('first_seen_activity_id'):
        vals.update(first_seen_activity_id=activity['id'], first_seen_by=activity['owner_id'], first_seen_at=at)
    committed_at = activity.get('first_committed_at')
    if activity.get('called') is True and committed_at and not reg.get('first_attempt_activity_id'):
        vals.update(first_attempt_activity_id=activity['id'], first_attempt_by=activity['owner_id'], first_attempt_at=committed_at)
    if activity.get('connected') is True and committed_at and not reg.get('first_connect_activity_id'):
        vals.update(first_connect_activity_id=activity['id'], first_connect_by=activity['owner_id'], first_connect_at=committed_at)
    c.execute(m.phone_registry.update().where(m.phone_registry.c.org_id == org_id, m.phone_registry.c.phone_key == reg['phone_key']).values(**vals))
    reg.update(vals)


def evaluate(c: Connection, org_id: uuid.UUID, reg: dict, activity: dict, *, historical: bool = False) -> DupResult:
    """Decide severity of saving `activity` against the registry state; raise flags and
    notifications. Must be called before record_contact for the same save."""
    owner = activity['owner_id']
    state = reg['state']
    other = _other_owner(reg)
    if state in PROTECTED and other and other != owner:
        sev, kind, notice = 'red', 'duplicate_lead', AGENT_NOTICE['protected']
    elif state in STRONG and other and other != owner:
        sev, kind, notice = 'amber', 'duplicate_strong', AGENT_NOTICE['strong']
    elif state in FOLLOWUP and other and other != owner:
        sev, kind, notice = 'amber', 'duplicate_followup', AGENT_NOTICE['followup']
    elif reg.get('first_seen_by') and reg['first_seen_by'] != owner:
        return DupResult('info', AGENT_NOTICE['seen'])
    else:
        return DupResult(None, AGENT_NOTICE[None])
    flag_id = uuid7()
    sups_new = notify.supervisors_of(c, owner, 2 if sev == 'red' else 1)
    sups_other = notify.supervisors_of(c, other, 2 if sev == 'red' else 1) if other else []
    assigned = sups_new[0] if sups_new else None
    c.execute(m.flags.insert().values(
        id=flag_id, org_id=org_id, severity=sev, kind=kind, phone_key=reg['phone_key'], activity_id=activity['id'],
        other_activity_id=reg.get('first_attempt_activity_id') or reg.get('first_seen_activity_id'), lead_id=reg.get('protected_lead_id'),
        raised_for_user=owner, other_user=other, assigned_to_supervisor=assigned,
        detail={'state': state, 'historical': historical}))
    if not historical:
        recipients = set(sups_new) | set(sups_other) | set(notify.super_admins(c, org_id))
        notify.notify(c, org_id, recipients, f'flag.{sev}', {'flag_id': str(flag_id), 'severity': sev, 'kind': kind,
                                                               'message': 'This number belongs to an active lead. Clarify to protect incentives.' if sev == 'red'
                                                               else 'A number already in follow-up was contacted by another agent.'})
        notify.notify(c, org_id, [owner], 'flag.neutral', {'message': notice})
    return DupResult(sev, notice, flag_id)


def set_state(c: Connection, org_id: uuid.UUID, phone_key: str | None, state: str, owner_id: uuid.UUID | None = None,
              lead_id: uuid.UUID | None = None, only_upgrade: bool = True) -> None:
    """Advance (or explicitly set) the pipeline state of a phone."""
    if not phone_key:
        return
    order = ['none', 'followup', 'strong_pending', 'strong_confirmed', 'lead', 'meeting', 'signed', 'client']
    reg = lock_registry(c, org_id, phone_key)
    if only_upgrade and order.index(state) < order.index(reg['state']):
        return
    vals: dict = {'state': state, 'state_owner_id': owner_id}
    if state in PROTECTED:
        vals.update(protected_lead_id=lead_id, protected_owner_id=owner_id)
    elif not only_upgrade:
        vals.update(protected_lead_id=None, protected_owner_id=None)
    c.execute(m.phone_registry.update().where(m.phone_registry.c.org_id == org_id, m.phone_registry.c.phone_key == phone_key).values(**vals))

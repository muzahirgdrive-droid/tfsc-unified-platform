"""Duplicate and conflict engine (§12) over the O(1) phone_registry (ADR-0005).

Called inside the save transaction. The registry row is locked with FOR UPDATE so
concurrent saves of the same number serialize; first-contact facts are therefore
decided by server commit order, never by row order or typed dates (§11.3).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, bindparam, text
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
    """Create-or-lock the registry row in one round trip: ON CONFLICT DO UPDATE takes the row lock (like FOR UPDATE)
    and RETURNING gives the current row. Inside a batch whose rows were locked up front (lock_registries) the row comes
    from that batch's cache: the same dict every time, so facts recorded by earlier rows of the batch are visible."""
    cache = c.info.get('registry_cache')
    if cache is not None and phone_key in cache:
        return cache[phone_key]
    return dict(c.execute(_LOCK, {'org_id': org_id, 'phone_key': phone_key}).mappings().one())


_LOCK = (insert(m.phone_registry).values(org_id=bindparam('org_id'), phone_key=bindparam('phone_key'), state='none')
         .on_conflict_do_update(index_elements=['org_id', 'phone_key'], set_={'updated_at': m.phone_registry.c.updated_at})
         .returning(*m.phone_registry.c))


def lock_registries(c: Connection, org_id: uuid.UUID, phones: list) -> dict[str, dict]:
    """Take the registry row locks for a whole batch up front, in phone-key order, in one statement. Two batches that
    share numbers would otherwise lock them in their own row orders and deadlock (seen under load with concurrent pastes).
    Returns the locked rows by key (see cached_registry). Rows created here for a number whose row is then rejected stay
    as state 'none' with no facts, which reads as new."""
    from tfsc_refine import refine_phone

    from .. import crypto
    keys = sorted({k for p in phones if p and (k := crypto.phone_key(refine_phone(p).e164))})
    if not keys:
        return {}
    rows = c.execute(insert(m.phone_registry).values([{'org_id': org_id, 'phone_key': k, 'state': 'none'} for k in keys])
                     .on_conflict_do_update(index_elements=['org_id', 'phone_key'], set_={'updated_at': m.phone_registry.c.updated_at})
                     .returning(*m.phone_registry.c)).mappings().all()
    return {r['phone_key']: dict(r) for r in rows}


@contextmanager
def cached_registry(c: Connection, rows: dict[str, dict]) -> Iterator[None]:
    """Serve lock_registry from rows this transaction already holds locked. Only for an all-or-nothing block: if a
    savepoint inside it rolled back, the cached facts could name a row that no longer exists."""
    c.info['registry_cache'] = rows
    try:
        yield
    finally:
        c.info.pop('registry_cache', None)


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
    # One prebuilt statement (COALESCE keeps facts that are already set); inside a batch it is deferred to the batch flush,
    # which is safe because this transaction holds the row lock and the updates run in order.
    params = {'org_id': org_id, 'phone_key': reg['phone_key'], 'at': at, 'aid': activity['id'], 'owner': activity['owner_id'],
              'attempt_at': committed_at if activity.get('called') is True and committed_at else None,
              'connect_at': committed_at if activity.get('connected') is True and committed_at else None}
    from . import rollups
    if not rollups.defer(c, _CONTACT, params):
        c.execute(_CONTACT, params)
    reg.update(vals)


_CONTACT = text(
    'UPDATE phone_registry SET last_activity_at = :at, updated_at = :at, '
    'first_seen_activity_id = COALESCE(first_seen_activity_id, :aid), first_seen_by = COALESCE(first_seen_by, :owner), '
    'first_seen_at = COALESCE(first_seen_at, :at), '
    'first_attempt_activity_id = CASE WHEN first_attempt_activity_id IS NULL AND CAST(:attempt_at AS timestamptz) IS NOT NULL '
    'THEN :aid ELSE first_attempt_activity_id END, '
    'first_attempt_by = CASE WHEN first_attempt_by IS NULL AND CAST(:attempt_at AS timestamptz) IS NOT NULL THEN :owner ELSE first_attempt_by END, '
    'first_attempt_at = COALESCE(first_attempt_at, CAST(:attempt_at AS timestamptz)), '
    'first_connect_activity_id = CASE WHEN first_connect_activity_id IS NULL AND CAST(:connect_at AS timestamptz) IS NOT NULL '
    'THEN :aid ELSE first_connect_activity_id END, '
    'first_connect_by = CASE WHEN first_connect_by IS NULL AND CAST(:connect_at AS timestamptz) IS NOT NULL THEN :owner ELSE first_connect_by END, '
    'first_connect_at = COALESCE(first_connect_at, CAST(:connect_at AS timestamptz)) '
    'WHERE org_id = :org_id AND phone_key = :phone_key')


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
    (c.info.get('registry_cache') or {}).pop(phone_key, None)  # the state changes in SQL below: re-read next time
    if only_upgrade and order.index(state) < order.index(reg['state']):
        return
    vals: dict = {'state': state, 'state_owner_id': owner_id}
    if state in PROTECTED:
        vals.update(protected_lead_id=lead_id, protected_owner_id=owner_id)
    elif not only_upgrade:
        vals.update(protected_lead_id=None, protected_owner_id=None)
    c.execute(m.phone_registry.update().where(m.phone_registry.c.org_id == org_id, m.phone_registry.c.phone_key == phone_key).values(**vals))

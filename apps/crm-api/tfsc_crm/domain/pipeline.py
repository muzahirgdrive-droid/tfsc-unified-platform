"""Pipeline state machine (§14), server-enforced, every transition audited.

follow_up --request--> strong_pending --Supervisor confirm--> strong_confirmed --convert--> qualified_lead (OWNED)
qualified_lead --meeting--> meeting --> signed_up --> agreement_in_progress --> client
any lead --lost(reason)--> lost --reopen(Supervisor+)--> qualified_lead
signed_up --reverse_sale(Supervisor+, reason)--> qualified_lead
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import Connection, and_, func, select
from sqlalchemy.exc import IntegrityError

from ..db import models as m
from ..ids import uuid7
from ..pacific import utcnow
from ..security.roles import SUPERVISOR, Actor, in_scope
from . import audit, duplicates, notify, outbox, rollups, scoring
from . import settings as cfg

LEAD_TRANSITIONS: dict[str, tuple[set[str], str]] = {
    'meeting': ({'qualified_lead'}, 'meeting'),
    'sign_up': ({'qualified_lead', 'meeting'}, 'signed_up'),
    'reverse_sale': ({'signed_up'}, 'qualified_lead'),
    'lost': ({'qualified_lead', 'meeting'}, 'lost'),
    'reopen': ({'lost'}, 'qualified_lead'),
    'agreement_started': ({'signed_up'}, 'agreement_in_progress'),
    'client': ({'agreement_in_progress', 'signed_up'}, 'client'),
}
REGISTRY_STATE = {'qualified_lead': 'lead', 'meeting': 'meeting', 'signed_up': 'signed', 'agreement_in_progress': 'signed', 'client': 'client'}


def _activity(c: Connection, actor: Actor, activity_id: uuid.UUID) -> dict:
    a = c.execute(select(m.activities).where(m.activities.c.id == activity_id, m.activities.c.org_id == actor.org_id,
                                             m.activities.c.deleted_at.is_(None))).mappings().first()
    if not a:
        raise HTTPException(404, 'Activity not found.')
    return dict(a)


def _visible_owner(c: Connection, actor: Actor, owner_id: uuid.UUID) -> bool:
    return owner_id == actor.id or (actor.rank >= SUPERVISOR and in_scope(c, actor, owner_id))


# ------------------------------------------------------------------ follow-ups
def followup_action(c: Connection, actor: Actor, followup_id: uuid.UUID, action: str, note: str | None, due_at: datetime | None, version: int) -> dict:
    f = c.execute(select(m.followups).where(m.followups.c.id == followup_id, m.followups.c.org_id == actor.org_id).with_for_update()).mappings().first()
    if not f:
        raise HTTPException(404, 'Follow-up not found.')
    allowed = _visible_owner(c, actor, f['owner_id'])
    if not allowed and actor.is_group_leader:
        from ..security.roles import group_member_ids
        allowed = c.execute(select(m.users.c.id).where(m.users.c.id == f['owner_id'], m.users.c.id.in_(group_member_ids(actor)))).first() is not None
    if not allowed:
        raise HTTPException(404, 'Follow-up not found.')
    if f['version'] != version:
        raise HTTPException(409, 'This follow-up changed. Refresh first.')
    now = utcnow()
    vals: dict = {'updated_at': now, 'version': f['version'] + 1}
    if action == 'done':
        vals.update(status='done', completed_by=actor.id, completed_at=now, outcome_note=note or f['outcome_note'])
    elif action == 'reschedule':
        if not due_at:
            raise HTTPException(400, 'Choose a new date and time.')
        vals.update(due_at=due_at, status='open')
    elif action == 'cancel':
        vals.update(status='cancelled')
    elif action == 'note':
        if not note:
            raise HTTPException(400, 'Write a note.')
    else:
        raise HTTPException(400, 'Unknown follow-up action.')
    if note:
        c.execute(m.followup_notes.insert().values(id=uuid7(), org_id=actor.org_id, followup_id=f['id'], author_id=actor.id, body=note[:4000]))
    c.execute(m.followups.update().where(m.followups.c.id == f['id']).values(**vals))
    if action in ('done', 'cancel') and f['phone_key']:
        still_open = c.execute(select(func.count()).select_from(m.followups).where(
            m.followups.c.org_id == actor.org_id, m.followups.c.phone_key == f['phone_key'], m.followups.c.status == 'open',
            m.followups.c.id != f['id'])).scalar_one()
        reg = c.execute(select(m.phone_registry.c.state).where(m.phone_registry.c.org_id == actor.org_id, m.phone_registry.c.phone_key == f['phone_key'])).scalar_one_or_none()
        if not still_open and reg == 'followup':
            duplicates.set_state(c, actor.org_id, f['phone_key'], 'none', None, only_upgrade=False)
    return {**dict(f), **vals}


# ------------------------------------------------------------------ strong follow-up
def request_strong(c: Connection, actor: Actor, activity_id: uuid.UUID, target_window: str | None, note: str | None) -> dict:
    a = _activity(c, actor, activity_id)
    if not _visible_owner(c, actor, a['owner_id']) and not actor.is_group_leader:
        raise HTTPException(404, 'Activity not found.')
    if a['called'] is not True or a['first_committed_at'] is None:
        raise HTTPException(409, 'A strong follow-up needs a committed call attempt.')
    if a['conflict'] in ('amber', 'red'):
        raise HTTPException(409, 'This number is under supervisor review (duplicate). It cannot be escalated until resolved.')
    existing = c.execute(select(m.strong_followups.c.id).where(m.strong_followups.c.activity_id == a['id'], m.strong_followups.c.decision.in_(['pending', 'confirmed']))).first()
    if existing:
        raise HTTPException(409, 'A strong follow-up already exists for this activity.')
    sups = notify.supervisors_of(c, a['owner_id'])
    sid = uuid7()
    now = utcnow()
    c.execute(m.strong_followups.insert().values(id=sid, org_id=actor.org_id, activity_id=a['id'], activity_date=a['business_date'],
                                                 phone_key=a['phone_key'], prospect_id=a['prospect_id'], requested_by=actor.id, requested_at=now,
                                                 supervisor_id=sups[0] if sups else None, target_window=(target_window or '')[:80] or None,
                                                 note=(note or '')[:4000] or None))
    duplicates.set_state(c, actor.org_id, a['phone_key'], 'strong_pending', a['owner_id'])
    rollups.bump(c, actor.org_id, a['owner_id'], now, strong_requested=1)
    notify.notify(c, actor.org_id, sups, 'strong.requested', {'strong_followup_id': str(sid), 'provider_name': a['provider_name']})
    audit.record(c, actor.org_id, actor.id, 'strong.requested', 'strong_followup', sid, None, {'activity_id': str(a['id'])})
    return {'id': sid, 'decision': 'pending'}


def decide_strong(c: Connection, actor: Actor, sid: uuid.UUID, decision: str, reason: str | None, version: int) -> dict:
    actor.require('strong.decide')
    s = c.execute(select(m.strong_followups).where(m.strong_followups.c.id == sid, m.strong_followups.c.org_id == actor.org_id).with_for_update()).mappings().first()
    if not s or not in_scope(c, actor, s['requested_by']):
        raise HTTPException(404, 'Strong follow-up not found.')
    if s['version'] != version:
        raise HTTPException(409, 'This request changed. Refresh first.')
    if s['decision'] != 'pending':
        raise HTTPException(409, 'This request was already decided.')
    if decision not in ('confirmed', 'rejected'):
        raise HTTPException(400, 'Decision must be confirmed or rejected.')
    if decision == 'rejected' and not (reason or '').strip():
        raise HTTPException(400, 'A reason is required to reject.')
    now = utcnow()
    c.execute(m.strong_followups.update().where(m.strong_followups.c.id == sid).values(
        decision=decision, decided_by=actor.id, decided_at=now, reason=reason, version=s['version'] + 1, updated_at=now))
    if decision == 'confirmed':
        duplicates.set_state(c, actor.org_id, s['phone_key'], 'strong_confirmed', s['requested_by'])
        scoring.rescore(c, actor.org_id, s['activity_id'], s['activity_date'], now)
        rollups.bump(c, actor.org_id, s['requested_by'], now, strong_confirmed=1)
    else:
        duplicates.set_state(c, actor.org_id, s['phone_key'], 'followup', s['requested_by'], only_upgrade=False)
    notify.notify(c, actor.org_id, [s['requested_by']], f'strong.{decision}', {'strong_followup_id': str(sid), 'reason': reason})
    audit.record(c, actor.org_id, actor.id, f'strong.{decision}', 'strong_followup', sid, {'decision': 'pending'}, {'decision': decision, 'reason': reason})
    return {'id': sid, 'decision': decision}


# ------------------------------------------------------------------ leads
def default_owner(c: Connection, actor: Actor, s: dict) -> tuple[uuid.UUID, str]:
    """§14.4: first connector within the protection window -> strong-FU requester -> round-robin (off)."""
    days = int(cfg.get(c, actor.org_id, 'lead_protection_days') or 90)
    if s['phone_key']:
        reg = c.execute(select(m.phone_registry).where(m.phone_registry.c.org_id == actor.org_id, m.phone_registry.c.phone_key == s['phone_key'])).mappings().first()
        if reg and reg['first_connect_by'] and reg['first_connect_at'] and reg['first_connect_at'] >= utcnow() - timedelta(days=days):
            active = c.execute(select(m.users.c.id).where(m.users.c.id == reg['first_connect_by'], m.users.c.status == 'active')).first()
            if active:
                return reg['first_connect_by'], 'first_connector'
    return s['requested_by'], 'strong_requester'


def convert_to_lead(c: Connection, actor: Actor, sid: uuid.UUID, owner_id: uuid.UUID | None, reason: str | None) -> dict:
    actor.require('leads.convert')
    s = c.execute(select(m.strong_followups).where(m.strong_followups.c.id == sid, m.strong_followups.c.org_id == actor.org_id).with_for_update()).mappings().first()
    if not s or not in_scope(c, actor, s['requested_by']):
        raise HTTPException(404, 'Strong follow-up not found.')
    if s['decision'] != 'confirmed':
        raise HTTPException(409, 'Confirm the strong follow-up before converting it to a lead.')
    if not s['phone_key']:
        raise HTTPException(409, 'Refine the phone number before converting to a lead.')
    auto_owner, rule = default_owner(c, actor, dict(s))
    chosen = owner_id or auto_owner
    if chosen != auto_owner and not (reason or '').strip():
        raise HTTPException(400, 'A reason is required to override the automatic owner.')
    if not in_scope(c, actor, chosen):
        raise HTTPException(403, 'The chosen owner is outside your scope.')
    a = c.execute(select(m.activities).where(m.activities.c.id == s['activity_id'], m.activities.c.business_date == s['activity_date'])).mappings().one()
    now = utcnow()
    lead_id = uuid7()
    sup = notify.supervisors_of(c, chosen)
    try:
        with c.begin_nested():
            c.execute(m.leads.insert().values(id=lead_id, org_id=actor.org_id, prospect_id=a['prospect_id'], origin_activity_id=a['id'],
                                              origin_activity_date=a['business_date'], phone_key=s['phone_key'], owner_id=chosen,
                                              supervisor_id=sup[0] if sup else actor.id, stage='qualified_lead', stage_changed_at=now,
                                              qualified_by=actor.id, qualified_at=now, domain_id=a['domain_id'], project_id=a['project_id']))
            c.execute(m.lead_ownership_claims.insert().values(org_id=actor.org_id, phone_key=s['phone_key'], lead_id=lead_id, owner_id=chosen, claimed_at=now))
    except IntegrityError:
        # Concurrent conversion: exactly one wins; the loser gets 409 and a red flag (§14.1, acceptance 8).
        c.execute(m.flags.insert().values(id=uuid7(), org_id=actor.org_id, severity='red', kind='duplicate_lead', phone_key=s['phone_key'],
                                          activity_id=a['id'], raised_for_user=chosen, assigned_to_supervisor=actor.id,
                                          detail={'reason': 'concurrent_conversion', 'strong_followup_id': str(sid)}))
        raise HTTPException(409, 'This number was just converted to a lead by someone else.') from None
    duplicates.set_state(c, actor.org_id, s['phone_key'], 'lead', chosen, lead_id)
    reg = c.execute(select(m.phone_registry).where(m.phone_registry.c.org_id == actor.org_id, m.phone_registry.c.phone_key == s['phone_key'])).mappings().one()
    facts = [('first_seen', reg['first_seen_by'], reg['first_seen_activity_id']), ('first_attempt', reg['first_attempt_by'], reg['first_attempt_activity_id']),
             ('first_connect', reg['first_connect_by'], reg['first_connect_activity_id']), ('nurture', s['requested_by'], s['activity_id']),
             ('qualifier', actor.id, None)]
    c.execute(m.attribution_ledger.insert(), [{'id': uuid7(), 'org_id': actor.org_id, 'lead_id': lead_id, 'user_id': u, 'role': role,
                                               'source_activity_id': src} for role, u, src in facts if u])
    c.execute(m.lead_assignments.insert().values(id=uuid7(), org_id=actor.org_id, lead_id=lead_id, from_user=None, to_user=chosen,
                                                 reason=reason or f'Automatic owner: {rule}', incentive_split={}, by_user=actor.id))
    scoring.rescore(c, actor.org_id, a['id'], a['business_date'], now)
    rollups.bump(c, actor.org_id, chosen, now, leads=1)
    audit.record(c, actor.org_id, actor.id, 'lead.converted', 'lead', lead_id, None, {'owner_id': str(chosen), 'rule': rule if chosen == auto_owner else 'override', 'reason': reason})
    notify.notify(c, actor.org_id, [chosen], 'lead.assigned', {'lead_id': str(lead_id)})
    return {'id': lead_id, 'owner_id': chosen, 'owner_rule': rule if chosen == auto_owner else 'override', 'stage': 'qualified_lead'}


def transition(c: Connection, actor: Actor, lead_id: uuid.UUID, action: str, reason: str | None, version: int) -> dict:
    if action not in LEAD_TRANSITIONS:
        raise HTTPException(400, 'Unknown transition.')
    actor.require('leads.sign' if action in ('sign_up', 'reverse_sale') else 'leads.convert')
    ld = c.execute(select(m.leads).where(m.leads.c.id == lead_id, m.leads.c.org_id == actor.org_id).with_for_update()).mappings().first()
    if not ld or not in_scope(c, actor, ld['owner_id']):
        raise HTTPException(404, 'Lead not found.')
    if ld['version'] != version:
        raise HTTPException(409, 'This lead changed. Refresh first.')
    allowed, target = LEAD_TRANSITIONS[action]
    if ld['stage'] not in allowed:
        raise HTTPException(409, f'Cannot {action.replace("_", " ")} from stage {ld["stage"]}.')
    if action in ('lost', 'reverse_sale', 'reopen') and not (reason or '').strip():
        raise HTTPException(400, 'A reason is required.')
    if ld['agreement_locked_at'] and action in ('reverse_sale', 'lost') and actor.rank < 4:
        raise HTTPException(403, 'Agreement Lock is engaged; only the Super Admin can change this lead.')
    now = utcnow()
    vals: dict = {'stage': target, 'stage_changed_at': now, 'version': ld['version'] + 1, 'updated_at': now}
    if action == 'sign_up':
        vals.update(signed_by=actor.id, signed_at=now)
    if action == 'reverse_sale':
        vals.update(signed_by=None, signed_at=None)
    if action == 'lost':
        vals['lost_reason'] = reason
        if c.execute(select(m.meetings.c.id).where(m.meetings.c.lead_id == lead_id, m.meetings.c.status == 'scheduled')).first():
            raise HTTPException(409, 'Cancel the scheduled meeting before marking this lead lost.')
    c.execute(m.leads.update().where(m.leads.c.id == lead_id).values(**vals))
    if action == 'lost':
        c.execute(m.lead_ownership_claims.delete().where(m.lead_ownership_claims.c.lead_id == lead_id))
        duplicates.set_state(c, actor.org_id, ld['phone_key'], 'none', None, only_upgrade=False)
    elif action == 'reopen':
        try:
            with c.begin_nested():
                c.execute(m.lead_ownership_claims.insert().values(org_id=actor.org_id, phone_key=ld['phone_key'], lead_id=lead_id, owner_id=ld['owner_id']))
        except IntegrityError:
            raise HTTPException(409, 'Another lead now owns this number.') from None
        duplicates.set_state(c, actor.org_id, ld['phone_key'], 'lead', ld['owner_id'], lead_id, only_upgrade=False)
    else:
        duplicates.set_state(c, actor.org_id, ld['phone_key'], REGISTRY_STATE[target], ld['owner_id'], lead_id, only_upgrade=action != 'reverse_sale')
    scoring.rescore(c, actor.org_id, ld['origin_activity_id'], ld['origin_activity_date'], now)
    if action == 'sign_up':
        rollups.bump(c, actor.org_id, ld['owner_id'], now, signups=1)
        c.execute(m.attribution_ledger.insert().values(id=uuid7(), org_id=actor.org_id, lead_id=lead_id, user_id=ld['owner_id'], role='closer'))
        c.execute(m.attribution_ledger.insert().values(id=uuid7(), org_id=actor.org_id, lead_id=lead_id, user_id=actor.id, role='supervisor'))
        _celebrate(c, actor, ld)
    if action == 'reverse_sale':
        rollups.bump(c, actor.org_id, ld['owner_id'], now, signups=-1)
    audit.record(c, actor.org_id, actor.id, f'lead.{action}', 'lead', lead_id, {'stage': ld['stage']}, {'stage': target, 'reason': reason})
    return {**dict(ld), **vals}


def _celebrate(c: Connection, actor: Actor, ld) -> None:
    agent = c.execute(select(m.users.c.display_name).where(m.users.c.id == ld['owner_id'])).scalar_one()
    tpl = cfg.get(c, actor.org_id, 'celebration').get('template') or cfg.DEFAULTS['celebration']['template']
    message = tpl.replace('{agent}', agent).replace('{supervisor}', actor.display_name).replace('{Agent Name}', agent).replace('{Supervisor Name}', actor.display_name)
    cid = uuid7()
    c.execute(m.celebrations.insert().values(id=cid, org_id=actor.org_id, lead_id=ld['id'], agent_id=ld['owner_id'], supervisor_id=actor.id, message=message))
    # No client name or details in the celebration (§18).
    outbox.emit(c, actor.org_id, 'sale.celebrated', {'celebration_id': str(cid), 'agent_id': str(ld['owner_id']), 'agent_name': agent,
                                                     'supervisor_name': actor.display_name, 'message': message}, key=str(ld['id']))


def reassign(c: Connection, actor: Actor, lead_id: uuid.UUID, to_user: uuid.UUID, reason: str, incentive_split: dict, version: int) -> dict:
    actor.require('leads.convert')
    if not (reason or '').strip():
        raise HTTPException(400, 'A reason is required to reassign a lead.')
    if not incentive_split:
        raise HTTPException(400, 'Record the incentive attribution (e.g. {"<old owner id>": 0.5, "<new owner id>": 0.5}).')
    total = sum(float(v) for v in incentive_split.values())
    if abs(total - 1.0) > 1e-6:
        raise HTTPException(400, 'Incentive shares must add up to 100%.')
    ld = c.execute(select(m.leads).where(m.leads.c.id == lead_id, m.leads.c.org_id == actor.org_id).with_for_update()).mappings().first()
    if not ld or not in_scope(c, actor, ld['owner_id']) or not in_scope(c, actor, to_user):
        raise HTTPException(404, 'Lead or user not found in your scope.')
    if ld['agreement_locked_at'] and actor.rank < 4:
        raise HTTPException(403, 'Agreement Lock is engaged; only the Super Admin can reassign.')
    if ld['version'] != version:
        raise HTTPException(409, 'This lead changed. Refresh first.')
    c.execute(m.leads.update().where(m.leads.c.id == lead_id).values(owner_id=to_user, version=ld['version'] + 1, updated_at=utcnow()))
    c.execute(m.lead_ownership_claims.update().where(m.lead_ownership_claims.c.lead_id == lead_id).values(owner_id=to_user))
    c.execute(m.lead_assignments.insert().values(id=uuid7(), org_id=actor.org_id, lead_id=lead_id, from_user=ld['owner_id'], to_user=to_user,
                                                 reason=reason, incentive_split=incentive_split, by_user=actor.id))
    for uid, share in incentive_split.items():
        c.execute(m.attribution_ledger.insert().values(id=uuid7(), org_id=actor.org_id, lead_id=lead_id, user_id=uuid.UUID(str(uid)), role='nurture', share=share))
    duplicates.set_state(c, actor.org_id, ld['phone_key'], REGISTRY_STATE.get(ld['stage'], 'lead'), to_user, lead_id, only_upgrade=False)
    audit.record(c, actor.org_id, actor.id, 'lead.reassigned', 'lead', lead_id, {'owner_id': str(ld['owner_id'])}, {'owner_id': str(to_user), 'reason': reason, 'split': incentive_split})
    return {'id': lead_id, 'owner_id': to_user}


__all__ = ['and_']

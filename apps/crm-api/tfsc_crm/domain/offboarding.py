"""One-click offboarding (§21): suspends CRM access and the TFSC grant, kills sessions, and moves the person's open
follow-ups and active leads to their supervisor's queue (open leads go round-robin to the team's agents instead when
the team has it on, §14.4). Lead moves are recorded as normal reassignments (lead_assignments + attribution: the
departing owner keeps their share of past work). Everything is audited."""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import Connection, select, update

from ..db import models as m
from ..ids import uuid7
from ..pacific import business_date, utcnow
from ..security.roles import Actor, in_scope
from . import assignment, audit, duplicates, notify, outbox

ACTIVE_LEAD_STAGES = ('qualified_lead', 'meeting', 'signed_up', 'agreement_in_progress')
REGISTRY_STATE = {'qualified_lead': 'lead', 'meeting': 'meeting', 'signed_up': 'signed', 'agreement_in_progress': 'signed'}


def offboard(c: Connection, actor: Actor, uid: uuid.UUID, reason: str) -> dict:
    actor.require('users.create')
    if uid == actor.id:
        raise HTTPException(400, 'You cannot offboard yourself.')
    u = c.execute(select(m.users).where(m.users.c.id == uid).with_for_update()).mappings().first()
    if not u or not in_scope(c, actor, uid):
        raise HTTPException(404, 'User not found.')
    if u['crm_role'] in ('director_admin', 'super_admin') and actor.rank < 4:
        raise HTTPException(403, 'Only the Super Admin can offboard a Director or Super Admin.')
    if u['status'] == 'offboarded':
        raise HTTPException(409, 'This person is already offboarded.')
    now = utcnow()
    queue_owner = u['manager_id'] or actor.id
    c.execute(update(m.users).where(m.users.c.id == uid).values(status='offboarded', left_on=business_date(now), version=u['version'] + 1, updated_at=now))
    sessions = c.execute(update(m.user_sessions).where(m.user_sessions.c.user_id == uid, m.user_sessions.c.revoked_at.is_(None))
                         .values(revoked_at=now)).rowcount
    # Follow-ups -> supervisor queue (history keeps who created them).
    fu = c.execute(update(m.followups).where(m.followups.c.owner_id == uid, m.followups.c.status == 'open')
                   .values(owner_id=queue_owner, updated_at=now)).rowcount
    # Active leads -> round-robin in the person's team (open leads, when on) else the supervisor, as audited
    # reassignments; the departing owner keeps full attribution of past work.
    team = assignment.team_of(c, uid)
    leads = c.execute(select(m.leads).where(m.leads.c.owner_id == uid, m.leads.c.stage.in_(ACTIVE_LEAD_STAGES)).with_for_update()).mappings().all()
    shared = 0
    for ld in leads:
        to = (assignment.pick(c, team, lock=True) if ld['stage'] in assignment.OPEN_STAGES else None) or queue_owner
        c.execute(m.leads.update().where(m.leads.c.id == ld['id']).values(owner_id=to, version=ld['version'] + 1, updated_at=now))
        c.execute(m.lead_ownership_claims.update().where(m.lead_ownership_claims.c.lead_id == ld['id']).values(owner_id=to))
        c.execute(m.lead_assignments.insert().values(id=uuid7(), org_id=actor.org_id, lead_id=ld['id'], from_user=uid, to_user=to,
                                                     reason=f'Offboarding{" (round-robin)" if to != queue_owner else ""}: {reason}'[:500],
                                                     incentive_split={str(uid): 1.0}, by_user=actor.id))
        duplicates.set_state(c, actor.org_id, ld['phone_key'], REGISTRY_STATE.get(ld['stage'], 'lead'), to, ld['id'], only_upgrade=False)
        if to != queue_owner:
            shared += 1
            notify.notify(c, actor.org_id, [to], 'lead.assigned', {'lead_id': str(ld['id'])})
    # TFSC grant: revoke locally now, deliver the revocation through the outbox (retried until TFSC confirms).
    grant = c.execute(select(m.module_grants).where(m.module_grants.c.user_id == uid, m.module_grants.c.module == 'agreements',
                                                    m.module_grants.c.revoked_at.is_(None))).mappings().first()
    if grant:
        c.execute(update(m.module_grants).where(m.module_grants.c.id == grant['id']).values(revoked_at=now, status='revoked'))
        outbox.emit(c, actor.org_id, 'user.offboarded', {
            'idempotency_key': f'{uid.hex[:16]}-offboard-{now:%Y%m%d%H%M%S}', 'action': 'revoke', 'crm_user_id': str(uid),
            'granting_crm_user_id': str(actor.id), 'email': u['email'], 'display_name': u['display_name'], 'role': grant['role'],
            'companies': grant['companies'], 'parent_admin_tfsc_user_id': None, 'link_tfsc_user_id': None, 'oidc_issuer': None, 'oidc_subject': None},
            key=str(uid))
    notify.notify(c, actor.org_id, [queue_owner], 'user.offboarded',
                  {'message': f"{u['display_name']} was offboarded. {fu} follow-up(s) and {len(leads) - shared} lead(s) moved to your queue"
                              + (f"; {shared} lead(s) shared out by round-robin." if shared else '.')})
    summary = {'sessions_revoked': sessions, 'followups_moved': fu, 'leads_moved': len(leads), 'leads_round_robin': shared,
               'agreements_revoked': bool(grant), 'queue_owner': str(queue_owner)}
    audit.record(c, actor.org_id, actor.id, 'user.offboarded', 'user', uid, {'status': u['status']}, {'reason': reason[:500], **summary})
    return summary

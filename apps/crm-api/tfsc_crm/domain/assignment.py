"""§14.4 rule 3: round-robin among a team's agents, weighted by open-lead capacity (configured per team, off by default).

Eligible: active sales agents with a current membership in the team who hold fewer open leads (qualified or in a
meeting) than the team's capacity. The pick is the agent with the fewest open leads; ties go to whoever received
a lead longest ago (then by id), so agents with equal load take turns. With `lock`, the team row is locked first,
so concurrent picks for one team serialize and can never push an agent past capacity.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import ColumnElement, Connection, func, or_, select

from ..db import models as m
from ..pacific import business_date

OPEN_STAGES = ('qualified_lead', 'meeting')
_NEVER = datetime.min.replace(tzinfo=UTC)


def _current(today: date) -> ColumnElement[bool]:
    tm = m.team_memberships
    return (tm.c.effective_from <= today) & or_(tm.c.effective_to.is_(None), tm.c.effective_to > today)


def team_of(c: Connection, user_id: uuid.UUID) -> dict | None:
    """The person's current team (kind 'team', active), if any."""
    tm, t = m.team_memberships, m.teams
    row = c.execute(select(t).join(tm, tm.c.team_id == t.c.id).where(tm.c.user_id == user_id, _current(business_date()), t.c.kind == 'team',
                                                                     t.c.active.is_(True)).order_by(tm.c.effective_from.desc()).limit(1)).mappings().first()
    return dict(row) if row else None


def supervised_team(c: Connection, supervisor_id: uuid.UUID) -> dict | None:
    t = m.teams
    row = c.execute(select(t).where(t.c.supervisor_id == supervisor_id, t.c.kind == 'team', t.c.active.is_(True)).order_by(t.c.name).limit(1)).mappings().first()
    return dict(row) if row else None


def pick(c: Connection, team: dict | None, *, lock: bool = False) -> uuid.UUID | None:
    if not team or not team['auto_assign']:
        return None
    if lock:
        c.execute(select(m.teams.c.id).where(m.teams.c.id == team['id']).with_for_update())
    u, ld, la, tm = m.users, m.leads, m.lead_assignments, m.team_memberships
    members = select(tm.c.user_id).where(tm.c.team_id == team['id'], _current(business_date()))
    open_leads = select(func.count()).where(ld.c.org_id == u.c.org_id, ld.c.owner_id == u.c.id, ld.c.stage.in_(OPEN_STAGES)).scalar_subquery()
    last = select(func.max(la.c.at)).where(la.c.to_user == u.c.id).scalar_subquery()
    rows = c.execute(select(u.c.id, open_leads.label('n'), last.label('last')).where(
        u.c.org_id == team['org_id'], u.c.id.in_(members), u.c.status == 'active', u.c.crm_role == 'sales_agent')).all()
    eligible = [r for r in rows if r.n < team['lead_capacity']]
    if not eligible:
        return None
    return min(eligible, key=lambda r: (r.n, r.last or _NEVER, str(r.id))).id

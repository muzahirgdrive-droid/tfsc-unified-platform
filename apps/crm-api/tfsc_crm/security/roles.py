"""Roles, capability matrix (§6.2) and reporting-line scope (§6.1). Server-enforced."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import Connection, Select, and_, or_, select

from ..db import models as m

RANK = {'none': -1, 'sales_agent': 0, 'supervisor': 1, 'manager': 2, 'director_admin': 3, 'super_admin': 4}
ROLE_LABEL = {'sales_agent': 'Sales Agent', 'supervisor': 'Supervisor', 'manager': 'Manager',
              'director_admin': 'Director (Admin)', 'super_admin': 'Super Admin', 'none': 'No sales access'}

AGENT, SUPERVISOR, MANAGER, DIRECTOR, SUPER = 0, 1, 2, 3, 4

# capability -> minimum rank (scope rules applied separately where the matrix says "scope")
CAPS: dict[str, int] = {
    'activities.own': AGENT,
    'strong.request': AGENT,
    'leaderboard.view': AGENT,
    'activities.edit_others': SUPERVISOR,
    'activities.edit_locked': SUPERVISOR,
    'activities.delete': SUPERVISOR,
    'strong.decide': SUPERVISOR,
    'leads.convert': SUPERVISOR,
    'leads.sign': SUPERVISOR,
    'meetings.view': SUPERVISOR,
    'flags.view': SUPERVISOR,
    'groups.manage': SUPERVISOR,
    'dashboard.customize': SUPERVISOR,
    'data_hunting.view': SUPERVISOR,
    'users.request': SUPERVISOR,
    'teams.manage': MANAGER,
    'users.create': MANAGER,
    'routing.view': MANAGER,
    'export.request': MANAGER,
    'users.appoint_supervisor': DIRECTOR,
    'users.appoint_manager': DIRECTOR,
    'agreements.grant_user': DIRECTOR,
    'settings.manage': DIRECTOR,
    'export.run': DIRECTOR,
    'security.read': DIRECTOR,
    'users.appoint_director': SUPER,
    'agreements.grant_admin': SUPER,
    'security.manage': SUPER,
    'flags.view_all': SUPER,
}


@dataclass
class Actor:
    id: uuid.UUID
    org_id: uuid.UUID
    role: str
    display_name: str
    email: str
    routing_view: bool = False
    led_group_ids: list[uuid.UUID] = field(default_factory=list)
    agreements_grant: dict | None = None
    employee_code: str | None = None
    csrf: str = ''
    session_hash: str = ''

    @property
    def rank(self) -> int:
        return RANK.get(self.role, -1)

    @property
    def is_group_leader(self) -> bool:
        return bool(self.led_group_ids)

    def can(self, cap: str) -> bool:
        if cap == 'routing.view':
            return self.rank >= MANAGER or (self.rank == SUPERVISOR and self.routing_view)
        if cap == 'followups.group':
            return self.rank >= SUPERVISOR or self.is_group_leader
        if cap == 'agreements.open':
            return bool(self.agreements_grant)
        need = CAPS.get(cap)
        if need is None:
            raise KeyError(f'Unknown capability {cap}')
        return self.rank >= need

    def require(self, cap: str) -> None:
        if not self.can(cap):
            raise HTTPException(403, 'You do not have permission for this action.')

    def capabilities(self) -> list[str]:
        caps = [c for c in CAPS if self.can(c)]
        for extra in ('routing.view', 'followups.group', 'agreements.open'):
            if self.can(extra) and extra not in caps:
                caps.append(extra)
        return sorted(caps)


def can_grant_role(actor: Actor, role: str) -> bool:
    """§6.2: nobody grants a role equal to or above their own except the Super Admin;
    Manager may create agents only; Director may appoint up to Manager."""
    target = RANK.get(role)
    if target is None:
        return False
    if actor.rank == SUPER:
        return True
    if actor.rank == DIRECTOR:
        return target <= MANAGER
    if actor.rank == MANAGER:
        return target <= AGENT
    return False


def scope_ids(actor: Actor) -> Select:
    """Self + recursive subordinates (reporting lines). Super Admin: whole org."""
    u = m.users
    if actor.rank >= SUPER:
        return select(u.c.id).where(u.c.org_id == actor.org_id)
    base = select(u.c.id).where(u.c.id == actor.id, u.c.org_id == actor.org_id).cte('scope_tree', recursive=True)
    if actor.rank >= SUPERVISOR:
        child = u.alias('scope_child')
        base = base.union_all(select(child.c.id).join(base, child.c.manager_id == base.c.id).where(child.c.org_id == actor.org_id))
    return select(base.c.id)


def group_member_ids(actor: Actor) -> Select:
    tm, t = m.team_memberships, m.teams
    return select(tm.c.user_id).join(t, t.c.id == tm.c.team_id).where(
        t.c.org_id == actor.org_id, t.c.id.in_(actor.led_group_ids or [uuid.UUID(int=0)]), tm.c.effective_to.is_(None))


def in_scope(c: Connection, actor: Actor, user_id: uuid.UUID | None) -> bool:
    if user_id is None:
        return False
    if user_id == actor.id:
        return True
    if actor.rank < SUPERVISOR:
        return False
    return c.execute(select(m.users.c.id).where(m.users.c.id == user_id, m.users.c.id.in_(scope_ids(actor)))).first() is not None


def owner_filter(actor: Actor, column, include_group: bool = False):
    """SQL predicate: rows owned by users in the actor's scope (agents: only self)."""
    if actor.rank < SUPERVISOR:
        cond = column == actor.id
        if include_group and actor.is_group_leader:
            cond = or_(cond, column.in_(group_member_ids(actor)))
        return cond
    return column.in_(scope_ids(actor))


__all__ = ['RANK', 'Actor', 'and_', 'can_grant_role', 'group_member_ids', 'in_scope', 'owner_filter', 'scope_ids']

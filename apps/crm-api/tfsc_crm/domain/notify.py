"""In-app notifications. Payloads must never contain meeting, domain or project data
for agents (§3, §15); callers pass role-appropriate payloads."""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import Connection, select

from ..db import models as m
from ..ids import uuid7

AGENT_FORBIDDEN_KEYS = {'meet_url', 'attendees', 'meeting', 'meetings', 'domain_id', 'project_id', 'project_owner', 'owner_email', 'starts_at'}


def notify(c: Connection, org_id: uuid.UUID, user_ids: Iterable[uuid.UUID | None], kind: str, payload: dict) -> None:
    ids = {u for u in user_ids if u}
    if not ids:
        return
    roles = dict(c.execute(select(m.users.c.id, m.users.c.crm_role).where(m.users.c.id.in_(ids))).all())
    rows = []
    for uid in ids:
        body = payload
        if roles.get(uid) in ('sales_agent', 'none'):
            body = {k: v for k, v in payload.items() if k not in AGENT_FORBIDDEN_KEYS}
        rows.append({'id': uuid7(), 'org_id': org_id, 'user_id': uid, 'kind': kind, 'payload': body})
    c.execute(m.notifications.insert(), rows)


def supervisors_of(c: Connection, user_id: uuid.UUID, levels: int = 1) -> list[uuid.UUID]:
    """Walk up the reporting line `levels` steps (supervisor, then manager...)."""
    out: list[uuid.UUID] = []
    cur = user_id
    for _ in range(levels):
        mgr = c.execute(select(m.users.c.manager_id).where(m.users.c.id == cur)).scalar_one_or_none()
        if not mgr:
            break
        out.append(mgr)
        cur = mgr
    return out


def super_admins(c: Connection, org_id: uuid.UUID) -> list[uuid.UUID]:
    return [r[0] for r in c.execute(select(m.users.c.id).where(m.users.c.org_id == org_id, m.users.c.crm_role == 'super_admin', m.users.c.status == 'active'))]

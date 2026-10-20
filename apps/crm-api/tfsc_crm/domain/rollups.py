"""Incremental rollups (§20.4). Dashboards read only these tables.

Each activity save applies (new contribution - old contribution) to agent_day_stats
and agent_hour_stats in the same transaction (O(1), keyed by agent/date, so writes
from different agents never contend). Event-time points come from the score ledger
(`points_event`); activity-date points (`points`) from activities.score_points.
`reconcile()` recomputes a day from raw rows and reports drift (nightly job).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Connection, select, text
from sqlalchemy.dialects.postgresql import insert

from ..db import models as m
from ..pacific import PACIFIC, business_date

DAY_FIELDS = ('attempts', 'connected', 'sms', 'emails', 'any_touch', 'research', 'no_call', 'points')


def contribution(a: dict | None) -> dict[str, int]:
    z = dict.fromkeys(DAY_FIELDS, 0)
    if not a or a.get('deleted_at'):
        return z
    committed = a.get('first_committed_at') is not None
    if not committed:
        z['research'] = 1
        return z
    if a.get('called') is True:
        z['attempts'] = 1
        z['connected'] = 1 if a.get('connected') else 0
        z['sms'] = 1 if a.get('text_sent') else 0
        z['emails'] = 1 if a.get('email_sent') else 0
        z['any_touch'] = 1 if (a.get('text_sent') or a.get('email_sent')) else 0
        z['points'] = int(a.get('score_points') or 0)
    else:
        z['no_call'] = 1
    return z


def _bump_day(c: Connection, org_id: uuid.UUID, user_id: uuid.UUID, d: date, delta: dict[str, int], at: datetime | None,
              domain_id=None, project_id=None) -> None:
    delta = {k: v for k, v in delta.items() if v}
    if not delta and at is None:
        return
    t = m.agent_day_stats
    ins = insert(t).values(org_id=org_id, user_id=user_id, business_date=d, domain_id=domain_id, project_id=project_id,
                           last_activity_at=at, **{k: v for k, v in delta.items()})
    upd = {k: t.c[k] + ins.excluded[k] for k in delta}
    upd['updated_at'] = text('now()')
    if at is not None:
        upd['last_activity_at'] = text('GREATEST(agent_day_stats.last_activity_at, excluded.last_activity_at)')
    c.execute(ins.on_conflict_do_update(index_elements=['org_id', 'user_id', 'business_date'], set_=upd))


def _bump_hour(c: Connection, org_id: uuid.UUID, user_id: uuid.UUID, d: date, hour: int, delta: dict[str, int]) -> None:
    delta = {k: v for k, v in delta.items() if v and k in ('attempts', 'connected', 'points')}
    if not delta:
        return
    t = m.agent_hour_stats
    ins = insert(t).values(org_id=org_id, user_id=user_id, business_date=d, hour=hour, **delta)
    c.execute(ins.on_conflict_do_update(index_elements=['org_id', 'user_id', 'business_date', 'hour'],
                                        set_={k: t.c[k] + ins.excluded[k] for k in delta}))


def on_activity(c: Connection, org_id: uuid.UUID, old: dict | None, new: dict, at: datetime, post_lock: bool = False) -> None:
    before, after = contribution(old), contribution(new)
    delta = {k: after[k] - before[k] for k in DAY_FIELDS}
    if post_lock:
        delta['post_lock_edits'] = 1
    if new.get('conflict') and not (old or {}).get('conflict'):
        delta['duplicates'] = 1
        if new['conflict'] == 'red':
            delta['red_flags'] = 1
    _bump_day(c, org_id, new['owner_id'], new['business_date'], delta, at, new.get('domain_id'), new.get('project_id'))
    committed_at = new.get('first_committed_at') or (old or {}).get('first_committed_at')
    if committed_at:
        local = committed_at.astimezone(PACIFIC)
        _bump_hour(c, org_id, new['owner_id'], local.date(), local.hour, delta)


def on_ledger(c: Connection, org_id: uuid.UUID, user_id: uuid.UUID, at: datetime, points: int) -> None:
    _bump_day(c, org_id, user_id, business_date(at), {'points_event': points}, None)


def bump(c: Connection, org_id: uuid.UUID, user_id: uuid.UUID, at: datetime, **counts: int) -> None:
    """Pipeline counters (followups_created, strong_requested, strong_confirmed, leads, signups)."""
    _bump_day(c, org_id, user_id, business_date(at), counts, None)


RECONCILE_SQL = """
SELECT owner_id AS user_id,
  count(*) FILTER (WHERE first_committed_at IS NOT NULL AND called IS TRUE) AS attempts,
  count(*) FILTER (WHERE first_committed_at IS NOT NULL AND called IS TRUE AND connected IS TRUE) AS connected,
  count(*) FILTER (WHERE first_committed_at IS NOT NULL AND called IS TRUE AND text_sent IS TRUE) AS sms,
  count(*) FILTER (WHERE first_committed_at IS NOT NULL AND called IS TRUE AND email_sent IS TRUE) AS emails,
  count(*) FILTER (WHERE first_committed_at IS NOT NULL AND called IS TRUE AND (text_sent IS TRUE OR email_sent IS TRUE)) AS any_touch,
  count(*) FILTER (WHERE first_committed_at IS NULL) AS research,
  count(*) FILTER (WHERE first_committed_at IS NOT NULL AND called IS NOT TRUE) AS no_call,
  coalesce(sum(score_points) FILTER (WHERE first_committed_at IS NOT NULL AND called IS TRUE), 0) AS points
FROM activities WHERE org_id = :org AND business_date = :d AND deleted_at IS NULL GROUP BY owner_id
"""


def reconcile(c: Connection, org_id: uuid.UUID, d: date, fix: bool = True) -> list[dict]:
    raw = {r['user_id']: dict(r) for r in c.execute(text(RECONCILE_SQL), {'org': org_id, 'd': d}).mappings()}
    t = m.agent_day_stats
    stored = {r['user_id']: dict(r) for r in c.execute(select(t).where(t.c.org_id == org_id, t.c.business_date == d)).mappings()}
    drift = []
    for uid in set(raw) | set(stored):
        want = {k: int(raw.get(uid, {}).get(k) or 0) for k in DAY_FIELDS}
        have = {k: int(stored.get(uid, {}).get(k) or 0) for k in DAY_FIELDS}
        if want != have:
            drift.append({'user_id': str(uid), 'date': d.isoformat(), 'expected': want, 'stored': have})
            if fix:
                _bump_day(c, org_id, uid, d, {k: want[k] - have[k] for k in DAY_FIELDS}, None)
    return drift

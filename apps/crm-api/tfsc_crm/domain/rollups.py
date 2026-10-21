"""Incremental rollups (§20.4). Dashboards read only these tables.

Each activity save applies (new contribution - old contribution) to agent_day_stats
and agent_hour_stats in the same transaction (O(1), keyed by agent/date, so writes
from different agents never contend). Event-time points come from the score ledger
(`points_event`); activity-date points (`points`) from activities.score_points.
`reconcile()` recomputes a day from raw rows and reports drift (nightly job).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

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


ALL_DAY_COLS = ('attempts', 'connected', 'sms', 'emails', 'any_touch', 'research', 'no_call', 'points', 'points_event', 'followups_created',
                'strong_requested', 'strong_confirmed', 'leads', 'signups', 'duplicates', 'red_flags', 'post_lock_edits')
HOUR_COLS = ('attempts', 'connected', 'points')


# ------------------------------------------------------------------ staged writes for multi-row saves
# A 1,000-row Refine & Save used to issue ~4,000 rollup upserts. Inside `batched(c)` the deltas are staged in memory
# and written with one statement per table at the end. Each row stages into its own buffer (`row(c)`), which is merged
# only when that row's savepoint commits, so a rejected row can never leave a delta behind.
def _new_stage() -> dict[str, Any]:
    return {'day': {}, 'hour': {}, 'ledger': [], 'inserts': [], 'deferred': []}


def defer(c: Connection, statement: Any, params: dict) -> bool:
    """Queue an idempotent-per-row write for the batch flush (executed in order). False when not batching."""
    st = stage(c)
    if st is None:
        return False
    st['deferred'].append((statement, params))
    return True


def defer_insert(c: Connection, statement: Any, params: dict) -> bool:
    """Queue a new row for one multi-row INSERT at the batch flush. Only on the all-or-nothing fast path (`batched(c,
    all_or_nothing=True)`): a failing INSERT then aborts the whole block, which is retried one savepoint per row with
    immediate writes. False otherwise (write now)."""
    batch = c.info.get('rollup_batch')
    st = stage(c)
    if not batch or not batch.get('all_or_nothing') or st is None:
        return False
    st['inserts'].append((statement, params))
    return True


def all_or_nothing(c: Connection) -> bool:
    batch = c.info.get('rollup_batch')
    return bool(batch and batch.get('all_or_nothing'))


def stage(c: Connection) -> dict[str, Any] | None:
    return c.info.get('rollup_row') or c.info.get('rollup_batch')


@contextmanager
def batched(c: Connection, *, all_or_nothing: bool = False) -> Iterator[None]:
    c.info['rollup_batch'] = {**_new_stage(), 'all_or_nothing': all_or_nothing}
    try:
        yield
        flush(c, c.info['rollup_batch'])
    finally:
        c.info.pop('rollup_batch', None)
        c.info.pop('rollup_row', None)


@contextmanager
def row(c: Connection) -> Iterator[None]:
    batch = c.info.get('rollup_batch')
    if batch is None:
        yield
        return
    c.info['rollup_row'] = _new_stage()
    try:
        yield
    except BaseException:
        c.info.pop('rollup_row', None)
        raise
    staged = c.info.pop('rollup_row')
    for key, e in staged['day'].items():
        _merge_day(batch['day'], key, e)
    for key, e in staged['hour'].items():
        tgt = batch['hour'].setdefault(key, dict.fromkeys(HOUR_COLS, 0))
        for k in HOUR_COLS:
            tgt[k] += e[k]
    batch['ledger'].extend(staged['ledger'])
    batch['inserts'].extend(staged['inserts'])
    batch['deferred'].extend(staged['deferred'])


def _merge_day(into: dict, key: tuple, e: dict) -> None:
    tgt = into.setdefault(key, {**dict.fromkeys(ALL_DAY_COLS, 0), 'domain_id': e['domain_id'], 'project_id': e['project_id'], 'last': None})
    for k in ALL_DAY_COLS:
        tgt[k] += e[k]
    if e['last'] is not None and (tgt['last'] is None or e['last'] > tgt['last']):
        tgt['last'] = e['last']
    tgt['domain_id'] = tgt['domain_id'] or e['domain_id']
    tgt['project_id'] = tgt['project_id'] or e['project_id']


_DAY_SQL = text(
    'INSERT INTO agent_day_stats (org_id, user_id, business_date, domain_id, project_id, last_activity_at, '  # noqa: S608 - fixed column names, built once
    + ', '.join(ALL_DAY_COLS) + ') VALUES (:org_id, :user_id, :business_date, :domain_id, :project_id, :last, '
    + ', '.join(f':{k}' for k in ALL_DAY_COLS) + ') ON CONFLICT (org_id, user_id, business_date) DO UPDATE SET '
    + ', '.join(f'{k} = agent_day_stats.{k} + EXCLUDED.{k}' for k in ALL_DAY_COLS)
    + ', last_activity_at = GREATEST(agent_day_stats.last_activity_at, EXCLUDED.last_activity_at), updated_at = now()')
_HOUR_SQL = text(
    'INSERT INTO agent_hour_stats (org_id, user_id, business_date, hour, attempts, connected, points) '
    'VALUES (:org_id, :user_id, :business_date, :hour, :attempts, :connected, :points) '
    'ON CONFLICT (org_id, user_id, business_date, hour) DO UPDATE SET attempts = agent_hour_stats.attempts + EXCLUDED.attempts, '
    'connected = agent_hour_stats.connected + EXCLUDED.connected, points = agent_hour_stats.points + EXCLUDED.points')


def flush(c: Connection, st: dict[str, Any]) -> None:
    days = [{'org_id': k[0], 'user_id': k[1], 'business_date': k[2], **e} for k, e in st['day'].items()
            if e['last'] is not None or any(e[col] for col in ALL_DAY_COLS)]
    if days:
        c.execute(_DAY_SQL, days)
    hours = [{'org_id': k[0], 'user_id': k[1], 'business_date': k[2], 'hour': k[3], **e} for k, e in st['hour'].items() if any(e.values())]
    if hours:
        c.execute(_HOUR_SQL, hours)
    if st['ledger']:
        c.execute(m.score_ledger.insert(), st['ledger'])
    # New rows first (activities, follow-ups: one multi-row INSERT per table), then the deferred updates. Writes are grouped
    # per statement, in their original order within each statement. That is safe for what is deferred today: the
    # registry's contact facts (record_contact) and its pipeline state (set_state) touch different columns.
    for key in ('inserts', 'deferred'):
        groups: dict[Any, list[dict]] = {}
        for statement, params in st[key]:
            groups.setdefault(statement, []).append(params)
        for statement, params_list in groups.items():
            c.execute(statement, params_list)
    st.update(_new_stage())


def _bump_day(c: Connection, org_id: uuid.UUID, user_id: uuid.UUID, d: date, delta: dict[str, int], at: datetime | None,
              domain_id=None, project_id=None) -> None:
    delta = {k: v for k, v in delta.items() if v}
    if not delta and at is None:
        return
    st = stage(c)
    if st is not None:
        _merge_day(st['day'], (org_id, user_id, d), {**dict.fromkeys(ALL_DAY_COLS, 0), **delta, 'domain_id': domain_id,
                                                     'project_id': project_id, 'last': at})
        return
    t = m.agent_day_stats
    ins = insert(t).values(org_id=org_id, user_id=user_id, business_date=d, domain_id=domain_id, project_id=project_id,
                           last_activity_at=at, **{k: v for k, v in delta.items()})
    upd: dict[str, Any] = {k: t.c[k] + ins.excluded[k] for k in delta}
    upd['updated_at'] = text('now()')
    if at is not None:
        upd['last_activity_at'] = text('GREATEST(agent_day_stats.last_activity_at, excluded.last_activity_at)')
    c.execute(ins.on_conflict_do_update(index_elements=['org_id', 'user_id', 'business_date'], set_=upd))


def _bump_hour(c: Connection, org_id: uuid.UUID, user_id: uuid.UUID, d: date, hour: int, delta: dict[str, int]) -> None:
    delta = {k: v for k, v in delta.items() if v and k in HOUR_COLS}
    if not delta:
        return
    st = stage(c)
    if st is not None:
        tgt = st['hour'].setdefault((org_id, user_id, d, hour), dict.fromkeys(HOUR_COLS, 0))
        for k, v in delta.items():
            tgt[k] += v
        return
    t = m.agent_hour_stats
    ins = insert(t).values(org_id=org_id, user_id=user_id, business_date=d, hour=hour, **delta)
    c.execute(ins.on_conflict_do_update(index_elements=['org_id', 'user_id', 'business_date', 'hour'],
                                        set_={k: t.c[k] + ins.excluded[k] for k in delta}))


def on_activity(c: Connection, org_id: uuid.UUID, old: dict | None, new: dict, at: datetime, post_lock: bool = False) -> None:
    before, after = contribution(old), contribution(new)
    delta = {k: after[k] - before[k] for k in DAY_FIELDS}
    hour_points = delta.pop('points')  # day-level points are applied once, by scoring.apply (see on_score)
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
        _bump_hour(c, org_id, new['owner_id'], local.date(), local.hour, {**delta, 'points': hour_points})


def on_score(c: Connection, org_id: uuid.UUID, user_id: uuid.UUID, at: datetime, activity_date: date, points: int) -> None:
    """A score change: event-time credit on the day it happened, activity-date credit on the activity's day."""
    _bump_day(c, org_id, user_id, business_date(at), {'points_event': points}, None)
    _bump_day(c, org_id, user_id, activity_date, {'points': points}, None)


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

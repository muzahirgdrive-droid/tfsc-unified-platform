"""Metric catalog (§20.3). Every number is computed from rollup tables (never raw history).

Definitions (shown on hover in the UI via CATALOG):
- Attempts = committed activities with Called = Yes, business_date in period.
- Quality % = Σ points ÷ (100 × Attempts).
- Calls/day/rep = Attempts ÷ (eligible workdays × active roster on those days); eligible = work calendar minus leave.
  The workbook variant ("per reporting rep": reps with > 0 calls) is shown separately and labelled.
- Composite = 0.50·Quality + 0.25·min(1, calls/day ÷ CallTarget) + 0.25·min(1, connRate ÷ ConnTarget).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Connection, and_, func, or_, select

from ..db import models as m
from ..pacific import business_date
from . import scoring
from . import settings as cfg

CATALOG: dict[str, str] = {
    'attempts': 'Committed activities with Called = Yes whose Pacific business date is in the period.',
    'connected': 'Attempts with Connected = Yes.',
    'conn_rate': 'Connected ÷ Attempts.',
    'sms': 'Attempts with Text sent = Yes.',
    'emails': 'Attempts with Email sent = Yes.',
    'any_touch': 'Attempts with Text or Email sent (each attempt counted once).',
    'research': 'Data-hunting rows (all outcomes Unset): no credit, no lock.',
    'research_conversion': 'Attempts ÷ (Attempts + data-hunting rows).',
    'points': 'Score ladder points (10/20/30/50/100, highest stage). Event-time by default; "credited to activity date" when toggled.',
    'quality': 'Σ points ÷ (100 × Attempts).',
    'calls_per_day': 'Attempts ÷ (eligible workdays × active roster). Eligible = Mon-Fri Pacific minus holidays and leave.',
    'calls_per_day_reporting': 'Workbook variant: Attempts ÷ (workdays × reps with > 0 attempts). Labelled "per reporting rep".',
    'composite': '0.50·Quality + 0.25·min(1, calls/day ÷ call target) + 0.25·min(1, connection rate ÷ connection target).',
    'band': 'Extraordinary ≥ 80% · Satisfactory ≥ 65% · Average ≥ 50% · Below Average ≥ 35% · Poor. Zero attempts → No activity.',
    'followups_created': 'Follow-ups created in the period.',
    'strong_requested': 'Strong follow-ups requested in the period.',
    'strong_confirmed': 'Strong follow-ups confirmed by a supervisor in the period.',
    'leads': 'Strong follow-ups converted to owned leads in the period.',
    'signups': 'Sales confirmed by a supervisor in the period (net of reversals).',
    'duplicates': 'Saved entries that hit another agent\'s follow-up/strong/lead (amber + red).',
    'red_flags': 'Entries on another agent\'s protected lead.',
    'post_lock_edits': 'Supervisor corrections after the lock window.',
}

PRESETS = ('today', 'yesterday', 'week', 'mtd', 'last_month', 'custom')
SUM_FIELDS = ('attempts', 'connected', 'sms', 'emails', 'any_touch', 'research', 'no_call', 'points', 'points_event', 'followups_created',
              'strong_requested', 'strong_confirmed', 'leads', 'signups', 'duplicates', 'red_flags', 'post_lock_edits')


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # inclusive
    label: str

    def previous(self) -> Period:
        days = (self.end - self.start).days + 1
        return Period(self.start - timedelta(days=days), self.start - timedelta(days=1), f'previous {days} day(s)')


def period(preset: str, start: date | None = None, end: date | None = None) -> Period:
    today = business_date()
    if preset == 'today':
        return Period(today, today, 'Today (PT)')
    if preset == 'yesterday':
        y = today - timedelta(days=1)
        return Period(y, y, 'Yesterday (PT)')
    if preset == 'week':
        s = today - timedelta(days=today.weekday())
        return Period(s, today, 'This week')
    if preset == 'mtd':
        return Period(today.replace(day=1), today, 'Month to date')
    if preset == 'last_month':
        first = today.replace(day=1)
        e = first - timedelta(days=1)
        return Period(e.replace(day=1), e, 'Last month')
    if preset == 'custom' and start and end and start <= end and (end - start).days <= 400:
        return Period(start, end, f'{start.isoformat()} – {end.isoformat()}')
    raise ValueError('Choose a valid period (custom ranges up to 400 days).')


def workdays(c: Connection, org_id: uuid.UUID, p: Period) -> list[date]:
    overrides = {r[0]: r[1] for r in c.execute(select(m.work_calendar.c.date, m.work_calendar.c.is_workday).where(
        m.work_calendar.c.org_id == org_id, m.work_calendar.c.date.between(p.start, p.end)))}
    out = []
    d = p.start
    while d <= p.end:
        if overrides.get(d, d.weekday() < 5):
            out.append(d)
        d += timedelta(days=1)
    return out


def roster_days(c: Connection, user_ids: list[uuid.UUID], days: list[date]) -> int:
    """Σ over workdays of active sales agents on that day (effective-dated by hire/leave/offboarding)."""
    if not user_ids or not days:
        return 0
    u = m.users
    rows = c.execute(select(u.c.id, u.c.hired_on, u.c.left_on, u.c.created_at, u.c.status).where(u.c.id.in_(user_ids), u.c.crm_role == 'sales_agent')).all()
    leave = {(r[0], r[1]) for r in c.execute(select(m.user_leave.c.user_id, m.user_leave.c.date).where(
        m.user_leave.c.user_id.in_(user_ids), m.user_leave.c.date.between(days[0], days[-1])))}
    total = 0
    for uid, hired, left, created, status in rows:
        start = hired or created.date()
        for d in days:
            if d >= start and (left is None or d <= left) and (uid, d) not in leave and not (status == 'offboarded' and left is None):
                total += 1
    return total


def aggregate(c: Connection, org_id: uuid.UUID, user_ids: list[uuid.UUID] | None, p: Period, filters: dict | None = None) -> dict[str, int]:
    t = m.agent_day_stats
    cond = [t.c.org_id == org_id, t.c.business_date.between(p.start, p.end)]
    if user_ids is not None:
        cond.append(t.c.user_id.in_(user_ids or [uuid.UUID(int=0)]))
    for k in ('domain_id', 'project_id'):
        if filters and filters.get(k):
            cond.append(t.c[k] == filters[k])
    row = c.execute(select(*[func.coalesce(func.sum(t.c[f]), 0).label(f) for f in SUM_FIELDS],
                           func.count(func.distinct(t.c.user_id)).filter(t.c.attempts > 0).label('reporting_reps')).where(and_(*cond))).mappings().one()
    return {k: int(v) for k, v in row.items()}


def kpis(c: Connection, org_id: uuid.UUID, user_ids: list[uuid.UUID] | None, roster_ids: list[uuid.UUID], p: Period, credit: str = 'event',
         filters: dict | None = None) -> dict[str, Any]:
    s = aggregate(c, org_id, user_ids, p, filters)
    days = workdays(c, org_id, p)
    tgt = cfg.target_for(c, org_id, p.end)
    attempts = s['attempts']
    points = s['points_event'] if credit == 'event' else s['points']
    quality = points / (100 * attempts) if attempts else 0.0
    rdays = roster_days(c, roster_ids, days)
    cpd = attempts / rdays if rdays else 0.0
    cpd_rep = attempts / (len(days) * s['reporting_reps']) if days and s['reporting_reps'] else 0.0
    conn_rate = s['connected'] / attempts if attempts else 0.0
    comp = scoring.composite(quality, cpd, conn_rate, tgt['call_target'], tgt['conn_target']) if attempts else None
    return {
        **{k: s[k] for k in SUM_FIELDS if k not in ('points', 'points_event')}, 'points': points, 'credit': credit,
        'conn_rate': round(conn_rate, 4), 'quality': round(quality, 4), 'calls_per_day': round(cpd, 2), 'calls_per_day_reporting': round(cpd_rep, 2),
        'research_conversion': round(attempts / (attempts + s['research']), 4) if attempts + s['research'] else 0.0,
        'composite': round(comp, 4) if comp is not None else None, 'band': scoring.band(comp, attempts, tgt['bands']),
        'workdays': len(days), 'roster_days': rdays, 'reporting_reps': s['reporting_reps'],
        'targets': {'call_target': tgt['call_target'], 'conn_target': tgt['conn_target']},
    }


def leaderboard(c: Connection, org_id: uuid.UUID, p: Period, limit: int, offset: int, credit: str = 'event') -> tuple[list[dict], int]:
    """Whole-org leaderboard (§23, owner request): metrics only, ranked server-side by composite."""
    t, u = m.agent_day_stats, m.users
    pts = t.c.points_event if credit == 'event' else t.c.points
    agg = select(t.c.user_id, func.sum(t.c.attempts).label('attempts'), func.sum(t.c.connected).label('connected'),
                 func.sum(pts).label('points')).where(t.c.org_id == org_id, t.c.business_date.between(p.start, p.end)).group_by(t.c.user_id).subquery()
    days = workdays(c, org_id, p)
    tgt = cfg.target_for(c, org_id, p.end)
    rows = c.execute(select(u.c.id, u.c.display_name, u.c.photo_object_key, func.coalesce(agg.c.attempts, 0), func.coalesce(agg.c.connected, 0),
                            func.coalesce(agg.c.points, 0)).outerjoin(agg, agg.c.user_id == u.c.id)
                     .where(u.c.org_id == org_id, u.c.crm_role == 'sales_agent', or_(u.c.status == 'active', agg.c.attempts > 0))).all()
    out = []
    for uid, name, photo, att, con, pts_ in rows:
        att, con, pts_ = int(att), int(con), int(pts_)
        q = pts_ / (100 * att) if att else 0.0
        cr = con / att if att else 0.0
        cpd = att / len(days) if days else 0.0
        comp = scoring.composite(q, cpd, cr, tgt['call_target'], tgt['conn_target']) if att else None
        out.append({'user_id': str(uid), 'name': name, 'has_photo': bool(photo), 'attempts': att, 'conn_rate': round(cr, 4), 'quality': round(q, 4),
                    'calls_per_day': round(cpd, 1), 'composite': round(comp, 4) if comp is not None else None, 'band': scoring.band(comp, att, tgt['bands']),
                    'points': pts_})
    out.sort(key=lambda r: (-(r['composite'] or -1), -r['attempts'], r['name']))
    for i, r in enumerate(out, 1):
        r['rank'] = i
    return out[offset:offset + limit], len(out)

"""Dashboards (§23): role-specific, read-only data, global filters, comparisons, layouts."""
from __future__ import annotations

import time
import uuid
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import Connection, and_, func, select, text
from sqlalchemy.dialects.postgresql import insert

from ..db import models as m
from ..db.session import read, system_tx, tx
from ..domain import metrics
from ..ids import uuid7
from ..pacific import PACIFIC, PAKISTAN, business_date, day_bounds, utcnow
from ..security.roles import MANAGER, SUPER, SUPERVISOR, Actor, group_member_ids, scope_ids
from .deps import ActorDep, parse_uuid
from .schemas import Model, OkOut, Out

router = APIRouter(prefix='/api/dashboard', tags=['dashboard'])

WIDGETS = {
    # id: minimum rank
    'kpi_attempts': 0, 'kpi_conn_rate': 0, 'kpi_points': 0, 'kpi_composite': 0, 'gauge_attempts': 0, 'hourly_sparkline': 0, 'my_followups': 0,
    'my_pipeline': 0, 'leaderboard': 0, 'celebrations': 0, 'streak': 0, 'group_board': 0, 'group_leaderboard': 0,
    'team_board': 1, 'funnel': 1, 'heatmap': 1, 'followup_sla': 1, 'strong_queue': 1, 'flag_counter': 1, 'hunting_vs_outreach': 1,
    'lock_compliance': 1, 'meetings_week': 1, 'target_trend': 1, 'kpi_signups': 1, 'kpi_quality': 1, 'kpi_calls_per_day': 1,
    'supervisor_comparison': 2, 'team_trends': 2, 'composite_distribution': 2, 'domain_project_treemap': 2, 'cohort_conversion': 2,
    'movers': 2, 'capacity': 2, 'system_health': 4, 'security_center': 4,
}
_cache: dict[tuple, tuple[float, Any]] = {}


def _cached(key: tuple, ttl: float, fn):
    """Short-TTL cache for widget payloads (§4.3). Keys always include the viewer's scope."""
    now = time.time()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    val = fn()
    if len(_cache) > 5000:
        _cache.clear()
    _cache[key] = (now + ttl, val)
    return val


def _scope(c: Connection, a: Actor, scope: str, team_id: str | None, agent_id: str | None) -> tuple[list[uuid.UUID] | None, list[uuid.UUID]]:
    """Return (user filter for rollups, roster ids). Agents only ever get themselves (or their group for GL)."""
    if agent_id:
        uid = parse_uuid(agent_id)
        if uid != a.id and not (a.rank >= SUPERVISOR and uid in _ids(c, select(m.users.c.id).where(m.users.c.id.in_(scope_ids(a))))):
            raise HTTPException(404, 'Agent not found in your scope.')
        return [uid], [uid]
    if scope == 'me' or (a.rank < SUPERVISOR and scope != 'group'):
        return [a.id], [a.id]
    if scope == 'group':
        if not a.is_group_leader and a.rank < SUPERVISOR:
            raise HTTPException(403, 'Group view is for group leaders and supervisors.')
        ids = _ids(c, group_member_ids(a)) if a.rank < SUPERVISOR else _ids(c, select(m.users.c.id).where(m.users.c.id.in_(scope_ids(a))))
        return ids, ids
    if team_id:
        tid = parse_uuid(team_id)
        ids = _ids(c, select(m.team_memberships.c.user_id).where(m.team_memberships.c.team_id == tid, m.team_memberships.c.effective_to.is_(None),
                                                                 m.team_memberships.c.user_id.in_(scope_ids(a))))
        return ids, ids
    if a.rank >= SUPER and scope == 'org':
        ids = _ids(c, select(m.users.c.id).where(m.users.c.org_id == a.org_id))
        return None, ids
    ids = _ids(c, select(m.users.c.id).where(m.users.c.id.in_(scope_ids(a))))
    return ids, ids


def _ids(c: Connection, stmt) -> list[uuid.UUID]:
    return [r[0] for r in c.execute(stmt)]


class SummaryOut(Out):
    period: dict[str, str]
    current: dict[str, Any]
    previous: dict[str, Any]
    definitions: dict[str, str]


@router.get('/summary', response_model=SummaryOut)
def summary(a: ActorDep, preset: str = 'today', start: date | None = None, end: date | None = None, scope: str = 'team', team_id: str | None = None,
            agent_id: str | None = None, credit: Literal['event', 'activity'] = 'event', domain_id: str | None = None, project_id: str | None = None) -> SummaryOut:
    try:
        p = metrics.period(preset, start, end)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    filters = {'domain_id': domain_id, 'project_id': project_id} if (domain_id or project_id) else None
    if filters and not a.can('routing.view'):
        raise HTTPException(403, 'Domain and project filters are for management.')
    with read(a.org_id, a.id) as c:
        users, roster = _scope(c, a, scope, team_id, agent_id)
        key = ('summary', str(a.id), preset, str(start), str(end), scope, team_id, agent_id, credit, domain_id, project_id)
        compute = lambda: (metrics.kpis(c, a.org_id, users, roster, p, credit, filters),  # noqa: E731
                           metrics.kpis(c, a.org_id, users, roster, p.previous(), credit, filters))
        # personal views are cheap and must reflect the agent's own save immediately; team/org views use a 5 s cache
        cur, prev = compute() if users is not None and len(users) <= 1 else _cached(key, 5, compute)
    return SummaryOut(period={'start': p.start.isoformat(), 'end': p.end.isoformat(), 'label': p.label}, current=cur, previous=prev,
                      definitions=metrics.CATALOG)


@router.get('/leaderboard')
def leaderboard(a: ActorDep, preset: str = 'today', start: date | None = None, end: date | None = None, page: int = 1, page_size: int = 50,
                credit: Literal['event', 'activity'] = 'event') -> dict:
    """Everyone sees the org leaderboard (metrics only, no client data)."""
    p = metrics.period(preset, start, end)
    page_size = max(1, min(page_size, 200))
    with read(a.org_id, a.id) as c:
        items, total = _cached(('lb', str(a.org_id), preset, str(start), str(end), page, page_size, credit), 10,
                               lambda: metrics.leaderboard(c, a.org_id, p, page_size, (max(page, 1) - 1) * page_size, credit))
    me = None
    if a.rank == 0:
        with read(a.org_id, a.id) as c:
            full, _ = metrics.leaderboard(c, a.org_id, p, 100000, 0, credit)
        me = next((r for r in full if r['user_id'] == str(a.id)), None)
    return {'items': items, 'total': total, 'page': page, 'page_size': page_size, 'me': me, 'period': p.label}


@router.get('/hourly')
def hourly(a: ActorDep, day: date | None = None, scope: str = 'me', team_id: str | None = None, agent_id: str | None = None) -> dict:
    """Hourly activity (Pacific hours, with PKT labels) for today's live charts."""
    d = day or business_date()
    with read(a.org_id, a.id) as c:
        users, _ = _scope(c, a, scope, team_id, agent_id)
        t = m.agent_hour_stats
        cond = [t.c.org_id == a.org_id, t.c.business_date == d] + ([t.c.user_id.in_(users)] if users is not None else [])
        rows = {r[0]: r for r in c.execute(select(t.c.hour, func.sum(t.c.attempts), func.sum(t.c.connected), func.sum(t.c.points)).where(and_(*cond)).group_by(t.c.hour))}
    from datetime import datetime
    series = []
    for h in range(24):
        r = rows.get(h)
        pt = datetime(d.year, d.month, d.day, h, tzinfo=PACIFIC)
        series.append({'hour_pt': h, 'label_pt': pt.strftime('%-I %p'), 'label_pkt': pt.astimezone(PAKISTAN).strftime('%-I %p'),
                       'attempts': int(r[1]) if r else 0, 'connected': int(r[2]) if r else 0, 'points': int(r[3]) if r else 0})
    return {'date': d.isoformat(), 'series': series}


@router.get('/series')
def series(a: ActorDep, metric: str = 'attempts', preset: str = 'mtd', start: date | None = None, end: date | None = None, scope: str = 'team',
           team_id: str | None = None, agent_id: str | None = None) -> dict:
    if metric not in metrics.SUM_FIELDS:
        raise HTTPException(400, 'Unknown metric.')
    p = metrics.period(preset, start, end)
    with read(a.org_id, a.id) as c:
        users, _ = _scope(c, a, scope, team_id, agent_id)
        t = m.agent_day_stats
        cond = [t.c.org_id == a.org_id, t.c.business_date.between(p.start, p.end)] + ([t.c.user_id.in_(users)] if users is not None else [])
        rows = dict(c.execute(select(t.c.business_date, func.sum(t.c[metric])).where(and_(*cond)).group_by(t.c.business_date)).all())
    out, d = [], p.start
    while d <= p.end:
        out.append({'date': d.isoformat(), 'value': int(rows.get(d, 0) or 0)})
        d += timedelta(days=1)
    return {'metric': metric, 'grain': 'day', 'definition': metrics.CATALOG.get(metric, ''), 'points': out}


@router.get('/team-board')
def team_board(a: ActorDep, team_id: str | None = None) -> dict:
    """Live team board for Supervisor+: status dot, attempts today, last activity, conn %, points, composite."""
    if a.rank < SUPERVISOR:
        raise HTTPException(403, 'You do not have permission for this action.')
    today = business_date()
    p = metrics.Period(today, today, 'Today (PT)')
    with read(a.org_id, a.id) as c:
        users, _ = _scope(c, a, 'team', team_id, None)
        tgt = metrics.cfg.target_for(c, a.org_id, today)
        t, u = m.agent_day_stats, m.users
        rows = c.execute(select(u.c.id, u.c.display_name, u.c.photo_object_key, t.c.attempts, t.c.connected, t.c.points_event, t.c.research,
                                t.c.last_activity_at, t.c.duplicates, t.c.red_flags)
                         .outerjoin(t, and_(t.c.user_id == u.c.id, t.c.business_date == today))
                         .where(u.c.id.in_(users or [uuid.UUID(int=0)]), u.c.crm_role == 'sales_agent', u.c.status == 'active')
                         .order_by(u.c.display_name)).mappings().all()
    now = utcnow()
    out = []
    for r in rows:
        att = r['attempts'] or 0
        q = (r['points_event'] or 0) / (100 * att) if att else 0.0
        cr = (r['connected'] or 0) / att if att else 0.0
        comp = metrics.scoring.composite(q, att, cr, tgt['call_target'], tgt['conn_target']) if att else None
        last = r['last_activity_at']
        status = 'active' if last and (now - last).total_seconds() < 900 else ('idle' if last else 'offline')
        out.append({'user_id': str(r['id']), 'name': r['display_name'], 'has_photo': bool(r['photo_object_key']), 'status': status,
                    'attempts': att, 'target': tgt['call_target'], 'conn_rate': round(cr, 4), 'points': r['points_event'] or 0,
                    'composite': round(comp, 4) if comp is not None else None, 'band': metrics.scoring.band(comp, att, tgt['bands']),
                    'research': r['research'] or 0, 'last_activity_at': last.isoformat() if last else None,
                    'duplicates': r['duplicates'] or 0, 'red_flags': r['red_flags'] or 0})
    return {'date': today.isoformat(), 'period': p.label, 'agents': out}


@router.get('/funnel')
def funnel(a: ActorDep, preset: str = 'mtd', start: date | None = None, end: date | None = None, scope: str = 'team', team_id: str | None = None) -> dict:
    if a.rank < SUPERVISOR:
        raise HTTPException(403, 'You do not have permission for this action.')
    p = metrics.period(preset, start, end)
    with read(a.org_id, a.id) as c:
        users, _roster = _scope(c, a, scope, team_id, None)
        s = metrics.aggregate(c, a.org_id, users, p)
        ld = m.leads
        lcond = [ld.c.org_id == a.org_id] + ([ld.c.owner_id.in_(users)] if users is not None else [])
        lo, hi = day_bounds(p.start)[0], day_bounds(p.end)[1]
        meetings = c.execute(select(func.count()).select_from(m.meetings).join(ld, ld.c.id == m.meetings.c.lead_id).where(
            and_(*lcond), m.meetings.c.created_at >= lo, m.meetings.c.created_at < hi)).scalar_one()
    stages = [('Attempts', s['attempts']), ('Connected', s['connected']), ('Touch', s['any_touch']), ('Strong FU', s['strong_confirmed']),
              ('Lead', s['leads']), ('Meeting', int(meetings)), ('Signed', s['signups'])]
    return {'period': p.label, 'stages': [{'stage': k, 'value': v} for k, v in stages]}


@router.get('/heatmap')
def heatmap(a: ActorDep, preset: str = 'last_month', start: date | None = None, end: date | None = None, scope: str = 'team', team_id: str | None = None) -> dict:
    """Hour × weekday attempts (Pacific)."""
    if a.rank < SUPERVISOR:
        raise HTTPException(403, 'You do not have permission for this action.')
    p = metrics.period(preset, start, end)
    with read(a.org_id, a.id) as c:
        users, _ = _scope(c, a, scope, team_id, None)
        t = m.agent_hour_stats
        cond = [t.c.org_id == a.org_id, t.c.business_date.between(p.start, p.end)] + ([t.c.user_id.in_(users)] if users is not None else [])
        dow = func.extract('isodow', t.c.business_date)
        rows = c.execute(select(dow, t.c.hour, func.sum(t.c.attempts)).where(and_(*cond)).group_by(dow, t.c.hour)).all()
    return {'period': p.label, 'cells': [{'weekday': int(d), 'hour_pt': int(h), 'attempts': int(v)} for d, h, v in rows]}


@router.get('/hunting-vs-outreach')
def hunting(a: ActorDep, preset: str = 'today', start: date | None = None, end: date | None = None, team_id: str | None = None) -> dict:
    a.require('data_hunting.view')
    p = metrics.period(preset, start, end)
    with read(a.org_id, a.id) as c:
        users, _ = _scope(c, a, 'team', team_id, None)
        t, u = m.agent_day_stats, m.users
        rows = c.execute(select(u.c.display_name, func.sum(t.c.research), func.sum(t.c.attempts), func.sum(t.c.no_call)).join(u, u.c.id == t.c.user_id)
                         .where(t.c.org_id == a.org_id, t.c.business_date.between(p.start, p.end), t.c.user_id.in_(users or [uuid.UUID(int=0)]))
                         .group_by(u.c.display_name).order_by(u.c.display_name)).all()
    return {'period': p.label, 'agents': [{'name': n, 'research': int(r or 0), 'attempts': int(at or 0), 'no_call': int(nc or 0)} for n, r, at, nc in rows]}


@router.get('/supervisors')
def supervisor_comparison(a: ActorDep, preset: str = 'mtd', start: date | None = None, end: date | None = None) -> dict:
    """Manager/Director: compare supervisors' teams (reporting lines)."""
    if a.rank < MANAGER:
        raise HTTPException(403, 'You do not have permission for this action.')
    p = metrics.period(preset, start, end)
    out = []
    with read(a.org_id, a.id) as c:
        sups = c.execute(select(m.users.c.id, m.users.c.display_name).where(m.users.c.id.in_(scope_ids(a)), m.users.c.crm_role == 'supervisor',
                                                                             m.users.c.status == 'active')).all()
        for sid, name in sups:
            agents = [r[0] for r in c.execute(select(m.users.c.id).where(m.users.c.manager_id == sid))]
            k = metrics.kpis(c, a.org_id, agents, agents, p)
            out.append({'supervisor_id': str(sid), 'supervisor': name, 'agents': len(agents), **{x: k[x] for x in (
                'attempts', 'conn_rate', 'quality', 'calls_per_day', 'composite', 'band', 'signups', 'leads')}})
    return {'period': p.label, 'supervisors': sorted(out, key=lambda r: -(r['composite'] or -1))}


@router.get('/breakdown')
def breakdown(a: ActorDep, by: Literal['domain', 'project'] = 'domain', preset: str = 'mtd', start: date | None = None, end: date | None = None) -> dict:
    """Domain/project treemap data (management only)."""
    if not a.can('routing.view'):
        raise HTTPException(403, 'You do not have permission for this action.')
    p = metrics.period(preset, start, end)
    col = m.agent_day_stats.c.domain_id if by == 'domain' else m.agent_day_stats.c.project_id
    with read(a.org_id, a.id) as c:
        t = m.agent_day_stats
        rows = c.execute(select(col, func.sum(t.c.attempts), func.sum(t.c.signups), func.sum(t.c.leads)).where(
            t.c.org_id == a.org_id, t.c.business_date.between(p.start, p.end), t.c.user_id.in_(scope_ids(a))).group_by(col)).all()
    return {'by': by, 'period': p.label, 'items': [{'key': k or 'unassigned', 'attempts': int(x or 0), 'signups': int(s or 0), 'leads': int(ld or 0)}
                                                  for k, x, s, ld in rows]}


@router.get('/system-health')
def system_health(a: ActorDep) -> dict:
    if a.rank < SUPER:
        raise HTTPException(403, 'You do not have permission for this action.')
    from ..realtime import broker
    with system_tx() as c:
        q: dict[str, int] = dict(c.execute(select(m.outbox.c.state, func.count()).group_by(m.outbox.c.state)).all())
        inbox_err = c.execute(select(func.count()).select_from(m.inbox).where(m.inbox.c.error.is_not(None))).scalar_one()
        size = c.execute(text('SELECT pg_database_size(current_database())')).scalar_one()
        oldest = c.execute(select(func.min(m.outbox.c.created_at)).where(m.outbox.c.state == 'pending')).scalar_one()
    return {'outbox': {k: int(v) for k, v in q.items()}, 'dead_letters': int(q.get('dead', 0)), 'inbox_errors': int(inbox_err),
            'oldest_pending_age_s': (utcnow() - oldest).total_seconds() if oldest else 0, 'sse_connections': broker().connections(),
            'database_bytes': int(size)}


# ------------------------------------------------------------------ layouts
class WidgetPos(Model):
    id: str = Field(max_length=60)
    x: int = Field(ge=0, le=48)
    y: int = Field(ge=0, le=500)
    w: int = Field(ge=1, le=48)
    h: int = Field(ge=1, le=40)
    filters: dict[str, str] = Field(default_factory=dict)


class LayoutIn(Model):
    widgets: list[WidgetPos] = Field(max_length=60)
    template_name: str | None = Field(default=None, max_length=80)


class LayoutOut(Out):
    widgets: list[dict[str, Any]]
    customizable: bool
    allowed_widgets: list[str]
    source: str


DEFAULT_LAYOUTS = {
    0: ['gauge_attempts', 'kpi_conn_rate', 'kpi_points', 'kpi_composite', 'hourly_sparkline', 'my_followups', 'my_pipeline', 'leaderboard', 'celebrations'],
    1: ['team_board', 'funnel', 'heatmap', 'followup_sla', 'strong_queue', 'flag_counter', 'hunting_vs_outreach', 'lock_compliance', 'meetings_week',
        'target_trend', 'leaderboard'],
    2: ['supervisor_comparison', 'team_trends', 'composite_distribution', 'domain_project_treemap', 'funnel', 'movers', 'capacity', 'leaderboard'],
    4: ['system_health', 'security_center', 'supervisor_comparison', 'funnel', 'domain_project_treemap', 'leaderboard'],
}


def _default(a: Actor) -> list[dict]:
    key = 4 if a.rank >= SUPER else 2 if a.rank >= MANAGER else 1 if a.rank >= SUPERVISOR else 0
    ids = list(DEFAULT_LAYOUTS[key])
    if a.rank == 0 and a.is_group_leader:
        ids[6:6] = ['group_board', 'group_leaderboard']
    return [{'id': w, 'x': (i % 3) * 4, 'y': (i // 3) * 4, 'w': 4, 'h': 4, 'filters': {}} for i, w in enumerate(ids)]


@router.get('/layout', response_model=LayoutOut)
def get_layout(a: ActorDep) -> LayoutOut:
    allowed = sorted(w for w, r in WIDGETS.items() if a.rank >= r)
    if a.rank < SUPERVISOR:  # agents and group leaders: fixed layout
        return LayoutOut(widgets=_default(a), customizable=False, allowed_widgets=allowed, source='fixed')
    with read(a.org_id, a.id) as c:
        mine = c.execute(select(m.dashboard_layouts.c.layout).where(m.dashboard_layouts.c.user_id == a.id)).scalar_one_or_none()
    if mine:
        return LayoutOut(widgets=[w for w in mine['widgets'] if w['id'] in allowed], customizable=True, allowed_widgets=allowed, source='saved')
    return LayoutOut(widgets=_default(a), customizable=True, allowed_widgets=allowed, source='template')


@router.put('/layout', response_model=LayoutOut)
def save_layout(body: LayoutIn, a: ActorDep) -> LayoutOut:
    a.require('dashboard.customize')
    allowed = {w for w, r in WIDGETS.items() if a.rank >= r}
    bad = [w.id for w in body.widgets if w.id not in allowed]
    if bad:
        raise HTTPException(400, f'Widgets not allowed: {", ".join(sorted(set(bad)))}')
    layout = {'widgets': [w.model_dump() for w in body.widgets]}
    with tx(a.org_id, a.id) as c:
        ins = insert(m.dashboard_layouts).values(id=uuid7(), org_id=a.org_id, user_id=a.id, layout=layout)
        c.execute(ins.on_conflict_do_update(index_elements=['user_id'], set_={'layout': layout, 'updated_at': utcnow()}))
    return LayoutOut(widgets=layout['widgets'], customizable=True, allowed_widgets=sorted(allowed), source='saved')


@router.delete('/layout', response_model=OkOut)
def reset_layout(a: ActorDep) -> OkOut:
    a.require('dashboard.customize')
    with tx(a.org_id, a.id) as c:
        c.execute(m.dashboard_layouts.delete().where(m.dashboard_layouts.c.user_id == a.id))
    return OkOut()


@router.get('/definitions')
def definitions(a: ActorDep) -> dict:
    return {'metrics': metrics.CATALOG, 'presets': list(metrics.PRESETS)}

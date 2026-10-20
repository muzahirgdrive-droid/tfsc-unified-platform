"""Owner's point-scoring ladder (§20.1) and event-time score ledger (§20.2, ADR-0016).

Ladder per committed activity with Called = Yes (highest stage, not additive):
10 attempted · 20 connected · 30 connected + (text OR email) · 50 strong follow-up confirmed · 100 signed up.
Called = No or data hunting -> 0. Conflict-marked entries are capped at 10 until resolved.
The ledger stores deltas when milestones happen; per activity the sum equals the ladder value.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Connection, func, select, text

from ..db import models as m
from ..ids import uuid7
from ..pacific import business_date, utcnow

RULE_VERSION = 'score.v1'
LEVELS: list[tuple[int, str]] = [(10, 'attempt'), (20, 'connect'), (30, 'touch'), (50, 'strong'), (100, 'signup')]


@dataclass(frozen=True)
class ScoreInput:
    committed: bool
    called: bool | None
    connected: bool | None
    text_sent: bool | None
    email_sent: bool | None
    strong_confirmed: bool = False
    signed_up: bool = False
    conflict: str | None = None
    deleted: bool = False


def ladder(s: ScoreInput) -> int:
    if s.deleted or not s.committed or s.called is not True:
        return 0
    if s.conflict in ('amber', 'red'):
        return 10
    if s.signed_up:
        return 100
    if s.strong_confirmed:
        return 50
    if s.connected and (s.text_sent or s.email_sent):
        return 30
    if s.connected:
        return 20
    return 10


def deltas(old: int, new: int) -> list[tuple[int, str]]:
    """Ledger rows to move an activity from `old` to `new` points."""
    if new == old:
        return []
    if new < old:
        return [(new - old, 'reversal')]
    out = []
    prev = old
    for level, reason in LEVELS:
        if old < level <= new:
            out.append((level - prev, reason))
            prev = level
    return out


def stage_label(points: int, committed: bool, called: bool | None) -> str:
    if not committed:
        return 'Data hunting'
    if called is not True:
        return 'No call'
    return {10: 'Attempted', 20: 'Connected', 30: 'Touch + Connect', 50: 'Strong Follow-Up', 100: 'Sign Up'}.get(points, 'Attempted')


def apply(c: Connection, org_id: uuid.UUID, activity: dict, new_points: int, at: datetime | None = None) -> int:
    """Write ledger deltas for an activity and update activities.score_points. Returns the delta."""
    old = int(activity.get('score_points') or 0)
    rows = deltas(old, new_points)
    if not rows:
        return 0
    at = at or utcnow()
    bd = business_date(at)
    c.execute(text('SELECT crm_ensure_partition_for(:p, :d)'), {'p': 'score_ledger', 'd': bd})
    c.execute(m.score_ledger.insert(), [{
        'id': uuid7(), 'org_id': org_id, 'activity_id': activity['id'], 'activity_date': activity['business_date'],
        'user_id': activity['owner_id'], 'business_date': bd, 'delta_points': d, 'reason': r, 'rule_version': RULE_VERSION, 'at': at,
    } for d, r in rows])
    c.execute(m.activities.update().where(m.activities.c.id == activity['id'], m.activities.c.business_date == activity['business_date'])
              .values(score_points=new_points))
    from . import rollups
    rollups.on_ledger(c, org_id, activity['owner_id'], at, new_points - old)
    return new_points - old


def milestones(c: Connection, activity_id: uuid.UUID) -> tuple[bool, bool]:
    """(strong_confirmed, signed_up) for an activity, from supervisor-confirmed records only."""
    strong = c.execute(select(func.count()).select_from(m.strong_followups).where(
        m.strong_followups.c.activity_id == activity_id, m.strong_followups.c.decision == 'confirmed')).scalar_one() > 0
    lead_stage = c.execute(select(m.leads.c.stage).where(m.leads.c.origin_activity_id == activity_id)).scalar_one_or_none()
    signed = lead_stage in ('signed_up', 'agreement_in_progress', 'client')
    if lead_stage in ('qualified_lead', 'meeting') or signed:
        strong = True
    return strong, signed


def rescore(c: Connection, org_id: uuid.UUID, activity_id: uuid.UUID, activity_date: date, at: datetime | None = None) -> int:
    a = c.execute(select(m.activities).where(m.activities.c.id == activity_id, m.activities.c.business_date == activity_date)).mappings().first()
    if not a:
        return 0
    strong, signed = milestones(c, activity_id)
    pts = ladder(ScoreInput(committed=a['first_committed_at'] is not None, called=a['called'], connected=a['connected'],
                            text_sent=a['text_sent'], email_sent=a['email_sent'], strong_confirmed=strong, signed_up=signed,
                            conflict=a['conflict'], deleted=a['deleted_at'] is not None))
    return apply(c, org_id, dict(a), pts, at)


def composite(quality: float, calls_per_day: float, conn_rate: float, call_target: float, conn_target: float) -> float:
    vol = min(1.0, calls_per_day / call_target) if call_target else 0.0
    conn = min(1.0, conn_rate / conn_target) if conn_target else 0.0
    return 0.5 * quality + 0.25 * vol + 0.25 * conn


def band(score: float | None, attempts: int, bands: dict[str, float]) -> str:
    if not attempts:
        return 'No activity'
    assert score is not None
    if score >= bands.get('extraordinary', 0.80):
        return 'Extraordinary'
    if score >= bands.get('satisfactory', 0.65):
        return 'Satisfactory'
    if score >= bands.get('average', 0.50):
        return 'Average'
    if score >= bands.get('below_average', 0.35):
        return 'Below Average'
    return 'Poor'

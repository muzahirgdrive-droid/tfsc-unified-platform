"""Prospective target revisions; historical event rows remain the report source."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import Table, Column, Integer, String, Float, select
import crm_db as db

PACIFIC = ZoneInfo('America/Los_Angeles')
versions = Table('crm_target_versions', db.meta,
    Column('version', Integer, primary_key=True), Column('effective_date', String(10), nullable=False, index=True),
    Column('call_target', Integer, nullable=False), Column('connection_target', Float, nullable=False))

def initialize(c):
    if not c.execute(select(versions.c.version).limit(1)).first():
        p = db.policy(c)
        c.execute(versions.insert().values(version=p['version'], effective_date='1900-01-01',
            call_target=p['call_target'], connection_target=p['connection_target']))

def schedule(c, policy):
    # A new target starts on the next Pacific business date, never retroactively.
    effective = (datetime.now(PACIFIC).date() + timedelta(days=1)).isoformat()
    c.execute(versions.insert().values(version=policy['version'], effective_date=effective,
        call_target=int(policy['call_target']), connection_target=float(policy['connection_target'])))
    return effective

def calendar(c, start, end):
    rows = [dict(r) for r in c.execute(select(versions).where(versions.c.effective_date <= end)
        .order_by(versions.c.effective_date, versions.c.version)).mappings()]
    current = rows[0]; index = 0; days = []; d = date.fromisoformat(start)
    last = min(date.fromisoformat(end), datetime.now(PACIFIC).date())
    while d <= last:
        while index + 1 < len(rows) and rows[index + 1]['effective_date'] <= d.isoformat():
            index += 1; current = rows[index]
        if d.weekday() < 5:
            days.append((d.isoformat(), current['call_target'], current['connection_target'], current['version']))
        d += timedelta(days=1)
    return days

def target(days, eligible):
    selected = [d for d in days if d[0] >= eligible]
    calls = sum(d[1] for d in selected)
    connections = sum(d[1] * d[2] for d in selected) / calls if calls else .6
    return calls, connections

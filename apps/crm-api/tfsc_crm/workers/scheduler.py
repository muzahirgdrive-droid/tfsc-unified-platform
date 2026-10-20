"""Periodic jobs (§12 SLA, §13 escalation, §20.4 nightly reconciliation, §21 audit anchor, partitions, sessions).

Each job runs at most once per period across all nodes: a run is claimed by inserting (job, period_key) into
`job_runs` (ON CONFLICT DO NOTHING). Jobs are idempotent anyway, so a crashed run is simply retried next period.

Single node: started in-process by the app (CRM_INLINE_SCHEDULER, default on outside hosted mode).
Hosted: run `python -m tfsc_crm.workers.scheduler` as its own process (one or more replicas).
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert

from ..db import models as m
from ..db.session import system_tx
from ..pacific import PACIFIC, business_date, utcnow

log = logging.getLogger('tfsc_crm.scheduler')


@dataclass(frozen=True)
class Job:
    name: str
    period: Callable[[datetime], str]  # period key for "now"; one run per key
    run: Callable[[datetime], dict]


def _every(minutes: int) -> Callable[[datetime], str]:
    return lambda now: now.strftime('%Y%m%d%H') + f'{now.minute // minutes:02d}'


def _daily_pt(hour: int) -> Callable[[datetime], str]:
    """Once per Pacific day, not before `hour` (returns '' before then = not due)."""
    def key(now: datetime) -> str:
        local = now.astimezone(PACIFIC)
        return local.date().isoformat() if local.hour >= hour else ''
    return key


def _orgs(c) -> list:
    return [r[0] for r in c.execute(select(m.orgs.c.id))]


def _with_org(c, org_id) -> None:
    """Jobs run as the migration/system role; scope helpers that read app.org_id still get the right org."""
    c.execute(text("SELECT set_config('app.org_id', :o, true)"), {'o': str(org_id)})


def job_flag_sla(now: datetime) -> dict:
    from ..domain import escalations
    n = 0
    with system_tx() as c:
        for org in _orgs(c):
            _with_org(c, org)
            n += escalations.flag_sla(c, org, now)
    return {'escalated': n}


def job_followups(now: datetime) -> dict:
    from ..domain import escalations
    n = 0
    with system_tx() as c:
        for org in _orgs(c):
            _with_org(c, org)
            n += escalations.followups_overdue(c, org, now)
    return {'escalated': n}


def job_audit_anchor(now: datetime) -> dict:
    from ..domain import audit
    day = business_date(now) - timedelta(days=1)
    from ..domain.bootstrap import data_dir
    sink = data_dir() / 'audit-anchors'
    out = 0
    with system_tx() as c:
        for org in _orgs(c):
            if audit.anchor(c, org, day, sink):
                out += 1
    return {'anchored': out, 'day': day.isoformat()}


def job_reconcile(now: datetime) -> dict:
    from ..domain import rollups
    day = business_date(now) - timedelta(days=1)
    drift = 0
    with system_tx() as c:
        for org in _orgs(c):
            found = rollups.reconcile(c, org, day, fix=True)
            drift += len(found)
            if found:
                log.warning('rollup drift fixed for %s on %s: %s rows', org, day, len(found))
    return {'drift_rows': drift, 'day': day.isoformat()}


def job_partitions(now: datetime) -> dict:
    with system_tx() as c:
        c.execute(text('SELECT crm_ensure_partitions(1, 3)'))
    return {}


def job_sessions(now: datetime) -> dict:
    with system_tx() as c:
        n = c.execute(m.user_sessions.delete().where(m.user_sessions.c.expires_at < now - timedelta(days=30))).rowcount
    return {'deleted': n}


JOBS: tuple[Job, ...] = (
    Job('flag_sla', _every(15), job_flag_sla),
    Job('followups_overdue', _every(60), job_followups),
    Job('audit_anchor', _daily_pt(0), job_audit_anchor),
    Job('rollup_reconcile', _daily_pt(1), job_reconcile),
    Job('partitions', _daily_pt(2), job_partitions),
    Job('session_cleanup', _daily_pt(3), job_sessions),
)


def run_due(now: datetime | None = None, only: set[str] | None = None) -> dict[str, dict]:
    """Run every job whose period has not been claimed yet. Returns {job: result} for jobs that ran."""
    now = now or utcnow()
    ran: dict[str, dict] = {}
    for job in JOBS:
        if only and job.name not in only:
            continue
        key = job.period(now)
        if not key:
            continue
        with system_tx() as c:
            claimed = c.execute(insert(m.job_runs).values(job=job.name, period_key=key, status='running')
                                .on_conflict_do_nothing().returning(m.job_runs.c.job)).first()
        if not claimed:
            continue
        try:
            detail = job.run(now)
            status = 'ok'
        except Exception as e:  # recorded; the next period retries
            log.exception('job %s failed', job.name)
            detail, status = {'error': type(e).__name__}, 'failed'
        with system_tx() as c:
            c.execute(update(m.job_runs).where(m.job_runs.c.job == job.name, m.job_runs.c.period_key == key)
                      .values(status=status, finished_at=utcnow(), detail=detail))
        ran[job.name] = {'status': status, **detail}
    return ran


def start_background(poll_s: float = 60.0) -> Callable[[], None]:
    stop = threading.Event()

    def loop() -> None:
        wait = 5.0  # first pass soon after start-up, then every poll_s
        while not stop.wait(wait):
            wait = poll_s
            try:
                run_due()
            except Exception:
                log.exception('scheduler loop error')

    t = threading.Thread(target=loop, name='scheduler', daemon=True)
    t.start()

    def _stop() -> None:
        stop.set()
        t.join(timeout=5)
    return _stop


if __name__ == '__main__':  # pragma: no cover - process entrypoint
    logging.basicConfig(level=logging.INFO)
    import time
    while True:
        run_due()
        time.sleep(30)

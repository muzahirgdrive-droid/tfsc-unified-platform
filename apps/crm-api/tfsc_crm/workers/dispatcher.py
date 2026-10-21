"""Outbox dispatcher: delivers outbox events at-least-once (§19.3, ADR-0008).

- realtime topics -> realtime broker (SSE fan-out);
- agreement/account topics -> TFSC inbox over the signed bridge (tfsc_crm.integrations.tfsc);
- sso.* topics -> Keycloak admin API (tfsc_crm.integrations.keycloak).
Rows are claimed with FOR UPDATE SKIP LOCKED so several dispatchers can run.
Failures back off exponentially; after MAX_ATTEMPTS a row goes to the dead-letter state
(admin retry UI in the Security Center).

Run standalone: python -m tfsc_crm.workers.dispatcher
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

from sqlalchemy import select, update

from ..db import models as m
from ..db.session import system_tx
from ..pacific import utcnow
from ..realtime import AUDIENCE_BY_TOPIC, broker

log = logging.getLogger('tfsc_crm.dispatcher')
MAX_ATTEMPTS = 12
REALTIME_TOPICS = set(AUDIENCE_BY_TOPIC)
TFSC_TOPICS = {'agreement.handoff.requested', 'account.provision.requested', 'user.offboarded'}


def _deliver(row: dict) -> None:
    topic = row['topic']
    if topic in REALTIME_TOPICS:
        payload = dict(row['payload'])
        payload.setdefault('audience', AUDIENCE_BY_TOPIC[topic])
        broker().publish(str(row['org_id']), {'id': row['id'], 'topic': topic, **payload})
        return
    if topic in TFSC_TOPICS:
        from ..integrations.tfsc import deliver
        deliver(row)
        return
    if topic.startswith('sso.'):
        from ..integrations import keycloak
        keycloak.deliver(row)
        return
    log.info('outbox topic %s has no consumer; marking delivered', topic)


def process_once(limit: int = 200) -> int:
    """Deliver due events; returns how many were processed."""
    now = utcnow()
    with system_tx() as c:
        rows = [dict(r) for r in c.execute(select(m.outbox).where(m.outbox.c.state == 'pending', m.outbox.c.next_attempt_at <= now)
                                           .order_by(m.outbox.c.id).limit(limit).with_for_update(skip_locked=True)).mappings()]
        for row in rows:
            try:
                _deliver(row)
                c.execute(update(m.outbox).where(m.outbox.c.id == row['id']).values(state='delivered', delivered_at=utcnow(), attempts=row['attempts'] + 1))
            except Exception as e:
                attempts = row['attempts'] + 1
                state = 'dead' if attempts >= MAX_ATTEMPTS else 'pending'
                delay = min(3600, 2 ** attempts)
                log.warning('outbox %s %s failed (%s): %s', row['id'], row['topic'], attempts, e)
                c.execute(update(m.outbox).where(m.outbox.c.id == row['id']).values(
                    attempts=attempts, state=state, last_error=str(e)[:2000], next_attempt_at=utcnow() + timedelta(seconds=delay)))
    return len(rows)


_wake = threading.Event()


def kick() -> None:
    """Wake the in-process dispatcher immediately (e.g. right after a sale is confirmed)."""
    _wake.set()


def start_background(poll_s: float = 0.25):
    """In-process dispatcher thread for single-node deployments and tests. Returns a stop function."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                if not process_once():
                    _wake.wait(poll_s)
                    _wake.clear()
            except Exception:
                log.exception('dispatcher loop error')
                stop.wait(2)

    t = threading.Thread(target=loop, name='outbox-dispatcher', daemon=True)
    t.start()

    def _stop() -> None:
        stop.set()
        _wake.set()
        t.join(timeout=5)
    return _stop


def run_forever(poll_s: float = 0.25) -> None:  # pragma: no cover - process entrypoint
    from ..telemetry import configure
    configure('tfsc-crm-dispatcher')
    while True:
        try:
            if not process_once():
                time.sleep(poll_s)
        except Exception:
            log.exception('dispatcher loop error')
            time.sleep(2)


if __name__ == '__main__':  # pragma: no cover
    run_forever()

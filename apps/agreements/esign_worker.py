"""Background loops for e-signature: send queued email, complete envelopes, reminders and expiry, read company inboxes.

Each kind of work has a lane of its own, so a slow mail server never delays signed copies, and a burst of signed envelopes
never delays invitations:

    mail      queued email (one connection per company mailbox, mailboxes side by side)
    complete  signed copies and certificates
    sweep     reminders, expiry, inbox reading, Trash purge, tidy-up, sealing of the history

Production runs the lanes as separate processes (worker.py); the local edition and the tests run them as threads or call
tick() directly. Every lane records a heartbeat, which the System status strip and the container health check read.
"""
import logging
import os
import socket
import threading
import time

import client_trash
import database as db
import esign
import housekeeping
import mailer

log = logging.getLogger('tfsc.esign.worker')

# name: (seconds between runs, job)
LANES = {
    'mail': (('mail', 0, mailer.dispatch_once),),
    'complete': (('complete', 0, esign.complete_due),),
    'sweep': (('sweep', 60, esign.sweep), ('inbox', 30, mailer.poll_inboxes), ('trash', 3600, client_trash.purge_once),
              ('tidy', 3600, housekeeping.run), ('seal', 120, esign.seal_backfill)),
}
ALL = tuple(task for lane in LANES.values() for task in lane)
HEARTBEAT_EVERY = 15


def tick(state: dict, tasks=ALL) -> int:
    """One pass. Each part is independent, so a failing mailbox never stops completion or reminders."""
    done = 0
    now = time.time()
    for name, every, job in tasks:
        if every and now - state.get(name, 0) < every:
            continue
        state[name] = now
        try:
            done += job() or 0
        except Exception:  # noqa: BLE001
            log.exception('e-signature background task %s failed', name)
    return done


def heartbeat(lane: str, state: dict | None = None) -> None:
    """Record that this lane is alive (at most every few seconds). Read by the deep health check."""
    state = state if state is not None else {}
    now = time.time()
    if now - state.get('_beat', 0) < HEARTBEAT_EVERY:
        return
    state['_beat'] = now
    key = f'heartbeat:{lane}'
    value = {'at': now, 'host': socket.gethostname(), 'pid': os.getpid()}
    try:
        with db.engine.begin() as c:
            if not c.execute(db.settings.update().where(db.settings.c.key == key).values(value=value)).rowcount:
                c.execute(db.settings.insert().values(key=key, value=value))
        beat = db.DATA / f'.heartbeat-{lane}'
        beat.write_text(str(now))
    except Exception:  # noqa: BLE001  (two processes creating the row at the same moment, or a brief database outage)
        log.debug('heartbeat for %s was not recorded', lane, exc_info=True)


def loop(lane: str, stop: threading.Event) -> None:
    """Run one lane until `stop` is set. The task in hand is always finished first."""
    state: dict = {}
    tasks = LANES[lane]
    while not stop.is_set():
        heartbeat(lane, state)
        if not tick(state, tasks):
            if lane == 'mail':
                mailer.wake.wait(2)
                mailer.wake.clear()
            else:
                stop.wait(2)


def start(lanes=('mail', 'complete', 'sweep'), stop: threading.Event | None = None) -> list[threading.Thread]:
    stop = stop or threading.Event()
    threads = []
    for lane in lanes:
        thread = threading.Thread(target=loop, args=(lane, stop), name=f'tfsc-{lane}', daemon=True)
        thread.start()
        threads.append(thread)
    return threads

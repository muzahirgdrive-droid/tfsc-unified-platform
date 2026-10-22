"""Background loop for e-signature: send queued email, complete envelopes, reminders and expiry, read company inboxes."""
import logging
import threading
import time

import esign
import mailer

log = logging.getLogger('tfsc.esign.worker')


def tick(state: dict) -> int:
    """One pass. Each part is independent, so a failing mailbox never stops completion or reminders."""
    done = 0
    now = time.time()
    for name, every, job in (('mail', 0, mailer.dispatch_once), ('complete', 0, esign.complete_due), ('sweep', 60, esign.sweep), ('inbox', 30, mailer.poll_inboxes)):
        if every and now - state.get(name, 0) < every:
            continue
        state[name] = now
        try:
            done += job() or 0
        except Exception:  # noqa: BLE001
            log.exception('e-signature background task %s failed', name)
    return done


def start() -> threading.Thread:
    def loop():
        state: dict = {}
        while True:
            if not tick(state):
                mailer.wake.wait(5)
                mailer.wake.clear()
    thread = threading.Thread(target=loop, name='tfsc-esign', daemon=True)
    thread.start()
    return thread

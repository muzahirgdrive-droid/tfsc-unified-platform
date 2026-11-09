"""Tidy-up jobs (run about once an hour by the background worker). Nothing here touches a client's records, a generated or
signed document, or the signing history: only leftovers and caches that can always be made again.

* staging folders and temporary files left behind by a crash or a timeout (older than a day)
* sign-in / rate-limit counters whose window is over
* sessions that ended
* the text of emails sent more than 90 days ago (who, when, subject and delivery result are kept)
* page images of envelopes that were finished long ago, and the oldest page images when the cache grows past its limit
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from sqlalchemy import delete, select, update

import database as db

log = logging.getLogger('tfsc.housekeeping')
DAY = 86400
MAIL_BODY_DAYS = int(os.getenv('TFSC_MAIL_BODY_DAYS', '90'))
PAGE_CACHE_DAYS = int(os.getenv('TFSC_PAGE_CACHE_DAYS', '30'))
PAGE_CACHE_MB = int(os.getenv('TFSC_PAGE_CACHE_MB', '2048'))
REMOVED_BODY = '[The text of this email is no longer kept. The record of who it went to, when and with what result is above.]'


def _old(path: Path, age: float, now: float) -> bool:
    try:
        return now - path.stat().st_mtime > age
    except OSError:
        return False


def leftovers(now: float | None = None) -> int:
    """Staging and temporary folders older than a day (a job that is still running is minutes old, never a day)."""
    now = now or time.time()
    removed = 0
    places = [(db.OUTPUT, ('.tfsc-*',)), (Path(tempfile.gettempdir()), ('tfsc-render-*', 'tfsc-upload-*', 'tfsc-page-*', 'tfsc-facts-*'))]
    for root, patterns in places:
        if not root.is_dir():
            continue
        for pattern in patterns:
            for path in root.glob(pattern):
                if _old(path, DAY, now):
                    shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)
                    removed += 1
    return removed


def database_rows(now: float | None = None) -> int:
    from esign_db import outbox, sign_sessions
    now = now or time.time()
    with db.engine.begin() as c:
        removed = c.execute(delete(db.throttle).where(db.throttle.c.until < now - 3600)).rowcount or 0
        removed += c.execute(delete(db.sessions).where(db.sessions.c.expires < now - 3600)).rowcount or 0
        removed += c.execute(delete(sign_sessions).where(sign_sessions.c.expires < now - 3600)).rowcount or 0
        if MAIL_BODY_DAYS:
            old = [r[0] for r in c.execute(select(outbox.c.id).where(outbox.c.state.in_(('sent', 'bounced', 'failed')), outbox.c.created < now - MAIL_BODY_DAYS * DAY,
                                                                   outbox.c.text != REMOVED_BODY).limit(2000))]
            if old:
                c.execute(update(outbox).where(outbox.c.id.in_(old)).values(text=REMOVED_BODY, html=''))
                removed += len(old)
    return removed


def page_cache(now: float | None = None) -> int:
    """Page images are drawn again on demand, so the cache only needs what is in use."""
    from esign_db import documents, envelopes
    now = now or time.time()
    root = db.DATA / 'esign-cache'
    if not root.is_dir():
        return 0
    folders = [p for p in root.iterdir() if p.is_dir()]
    removed = 0
    stale = [p for p in folders if _old(p, PAGE_CACHE_DAYS * DAY, now)]
    if stale:
        with db.engine.connect() as c:
            for i in range(0, len(stale), 500):
                names = [p.name for p in stale[i:i + 500]]
                active = set(c.execute(select(documents.c.id).join(envelopes, envelopes.c.id == documents.c.envelope_id)
                                       .where(documents.c.id.in_(names), envelopes.c.status.in_(('draft', 'sent', 'completing', 'template')))).scalars())
                for path in stale[i:i + 500]:
                    if path.name not in active:
                        shutil.rmtree(path, ignore_errors=True)
                        removed += 1
    # size cap: the folders nobody opened for longest go first
    sized = []
    for path in root.iterdir():
        if path.is_dir():
            try:
                sized.append((path.stat().st_mtime, sum(f.stat().st_size for f in path.iterdir() if f.is_file()), path))
            except OSError:
                continue
    total = sum(size for _, size, _ in sized)
    for _, size, path in sorted(sized):
        if total <= PAGE_CACHE_MB * 1024 * 1024:
            break
        shutil.rmtree(path, ignore_errors=True)
        total -= size
        removed += 1
    return removed


def run() -> int:
    done = 0
    for job in (leftovers, database_rows, page_cache):
        try:
            done += job() or 0
        except Exception:  # noqa: BLE001  (one tidy-up job never stops the others)
            log.exception('tidy-up job %s failed', job.__name__)
    if done:
        log.info('tidy-up removed %d leftover item(s)', done)
    return 0     # never keeps the worker loop busy

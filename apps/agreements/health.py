"""System status: is everything that works in the background actually working?

/api/health          quick: the application answers and reaches its database (used by the proxy and the installer)
/api/health/deep     everything below; HTTP 503 when something is broken (for an outside uptime monitor)
/api/system-status   the same list for the Super Admin's status strip on Home

Each check returns {'key', 'label', 'state': 'ok' | 'warn' | 'bad', 'detail', 'link'}; 'link' is the screen where the
problem is fixed. Nothing here changes data.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import socket
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

import database as db
import security as sec

LANES = (('generate', 'Document generation'), ('mail', 'Email sending'), ('complete', 'Signed copies'), ('sweep', 'Reminders and inbox reading'))
HEARTBEAT_LATE = 180          # seconds without a sign of life before a background lane counts as stopped
_cache: dict = {'at': 0.0, 'items': None}


def item(key: str, label: str, state: str, detail: str, link: str = '') -> dict:
    return {'key': key, 'label': label, 'state': state, 'detail': detail, 'link': link}


def clock(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.UTC).strftime('%b %d, %H:%M UTC').replace(' 0', ' ')


def ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f'{seconds} seconds'
    if seconds < 5400:
        return f'{round(seconds / 60)} minutes'
    if seconds < 36 * 3600:
        return f'{round(seconds / 3600)} hours'
    return f'{round(seconds / 86400)} days'


def check_workers(c, now: float) -> list[dict]:
    beats = dict(c.execute(select(db.settings.c.key, db.settings.c.value).where(db.settings.c.key.like('heartbeat:%'))).all())
    out = []
    for lane, label in LANES:
        beat = (beats.get(f'heartbeat:{lane}') or {}).get('at')
        if not beat:
            out.append(item(f'worker:{lane}', label, 'warn', 'Has not reported yet. It reports within a few minutes of starting.'))
        elif now - beat > HEARTBEAT_LATE:
            out.append(item(f'worker:{lane}', label, 'bad', f'Stopped: no sign of life for {ago(now - beat)}. Restart with: sudo ./agreements.sh restart'))
        else:
            out.append(item(f'worker:{lane}', label, 'ok', 'Running.'))
    return out


def check_mail(c, now: float) -> list[dict]:
    from entities import ENTITIES
    from esign_db import inbound, mailboxes, outbox
    out = []
    waiting = {r[0]: (r[1], r[2]) for r in c.execute(select(outbox.c.entity, func.count(), func.min(outbox.c.created)).where(outbox.c.state.in_(('queued', 'sending'))).group_by(outbox.c.entity))}
    failed = dict(c.execute(select(outbox.c.entity, func.count()).where(outbox.c.state == 'failed', outbox.c.created > now - 86400).group_by(outbox.c.entity)).all())
    set_aside = dict(c.execute(select(inbound.c.entity, func.count()).where(inbound.c.kind == 'quarantined', inbound.c.created > now - 7 * 86400).group_by(inbound.c.entity)).all())
    names = list(ENTITIES)
    for box in c.execute(select(mailboxes)).mappings():
        if box['mode'] == 'off' or box['entity'] not in ENTITIES:
            continue
        short = ENTITIES[box['entity']]['short']
        link = '#/admin/mailboxes'
        count, oldest = waiting.get(box['entity'], (0, None))
        queue = f' {count} email{"s" if count != 1 else ""} waiting; nothing is lost.' if count else ''
        if box['paused_until'] and box['paused_until'] > now:
            kind = box['pause_kind'] or ''
            state = 'bad' if kind in ('auth', 'connect') and (box['pause_count'] or 0) >= (1 if kind == 'auth' else 3) else 'warn'
            out.append(item(f'mail:{names.index(box["entity"])}', f'{short} mailbox', state,
                            f"Sending is paused until {clock(box['paused_until'])}: {box['pause_reason'] or 'the mail server was not available.'}{queue}", link))
        elif count and oldest and now - oldest > 900:
            out.append(item(f'mail:{names.index(box["entity"])}', f'{short} mailbox', 'warn', f'The oldest email has waited {ago(now - oldest)}.{queue}', link))
        elif box['imap_error']:
            out.append(item(f'mail:{names.index(box["entity"])}', f'{short} mailbox', 'warn', f"Replies and bounces are not being read: {box['imap_error']}", link))
        elif failed.get(box['entity']):
            n = failed[box['entity']]
            out.append(item(f'mail:{names.index(box["entity"])}', f'{short} mailbox', 'warn', f'{n} email{"s" if n != 1 else ""} could not be delivered in the last 24 hours. Open the envelopes marked "Needs attention".', '#/agreements/attention'))
        elif set_aside.get(box['entity']):
            n = set_aside[box['entity']]
            out.append(item(f'mail:{names.index(box["entity"])}', f'{short} mailbox', 'warn', f'{n} incoming message{"s" if n != 1 else ""} could not be processed automatically and {"were" if n != 1 else "was"} set aside. Read {"them" if n != 1 else "it"} in the {box["address"]} mailbox.', link))
        else:
            out.append(item(f'mail:{names.index(box["entity"])}', f'{short} mailbox', 'ok', 'Sending and reading replies.' if box['mode'] == 'mailbox' and box['imap_enabled'] else 'Sending.'))
    return out


def check_queues(c, now: float) -> list[dict]:
    from esign_db import envelopes
    out = []
    stuck = c.execute(select(func.count(), func.min(envelopes.c.updated)).where(envelopes.c.status == 'completing', envelopes.c.updated < now - 1800)).one()
    failing = c.execute(select(func.count()).where(envelopes.c.status == 'completing', envelopes.c.completion_attempts >= 5)).scalar()
    if failing:
        out.append(item('completion', 'Signed copies', 'bad', f'{failing} signed envelope{"s" if failing != 1 else ""} could not be completed after several tries. Open "Waiting for others" to see which; the server log has the reason.', '#/agreements/waiting'))
    elif stuck[0]:
        out.append(item('completion', 'Signed copies', 'warn', f'{stuck[0]} signed envelope{"s" if stuck[0] != 1 else ""} waiting for the signed copy for more than {ago(now - stuck[1])}.', '#/agreements/waiting'))
    queued = c.execute(select(func.count(), func.min(db.jobs.c.created)).where(db.jobs.c.state == 'queued')).one()
    if queued[0] and now - queued[1] > 600:
        out.append(item('generation', 'Document generation', 'warn', f'{queued[0]} agreement{"s" if queued[0] != 1 else ""} waiting to be generated, the oldest for {ago(now - queued[1])}.'))
    return out


def clamav(command: bytes, timeout: float = 4) -> str:
    target = os.getenv('TFSC_CLAMAV', '')
    host, _, port = target.rpartition(':')
    with socket.create_connection((host, int(port)), timeout=timeout) as s:
        s.sendall(b'z' + command + b'\0')
        reply = b''
        while not reply.endswith(b'\0'):
            data = s.recv(4096)
            if not data:
                break
            reply += data
    return reply.rstrip(b'\0').decode(errors='replace')


def signature_age(version: str, now: float) -> float | None:
    """Days since the virus signatures were published, from clamd's VERSION answer ("ClamAV 1.4.3/27800/Mon Oct 19 09:11:05 2026")."""
    parts = version.split('/')
    if len(parts) < 3:
        return None
    stamp = re.sub(r'\s+', ' ', parts[2].strip())
    for pattern in ('%a %b %d %H:%M:%S %Y', '%b %d %H:%M:%S %Y'):
        try:
            return (now - dt.datetime.strptime(stamp, pattern).replace(tzinfo=dt.UTC).timestamp()) / 86400
        except ValueError:
            continue
    return None


def check_scanner(now: float) -> list[dict]:
    if not os.getenv('TFSC_CLAMAV', ''):
        return []
    try:
        version = clamav(b'VERSION')
    except Exception:  # noqa: BLE001  (any failure to answer means: not reachable)
        return [item('scanner', 'Virus scanner', 'bad', 'Not reachable. Uploads and replies with attachments wait until it is back (nothing is accepted unscanned). It needs a few minutes after a restart.')]
    age = signature_age(version, now)
    if age is None:
        return [item('scanner', 'Virus scanner', 'ok', 'Running.')]
    if age > 3:
        return [item('scanner', 'Virus scanner', 'warn' if age <= 14 else 'bad', f'Running, but its virus signatures are {round(age)} days old. The server may not be able to reach database.clamav.net for updates.')]
    return [item('scanner', 'Virus scanner', 'ok', 'Running with current signatures.')]


def check_backups(c, now: float) -> list[dict]:
    """Written into the settings table by the backup service (tfsc-backup publish) every few minutes."""
    value = c.execute(select(db.settings.c.value).where(db.settings.c.key == 'ops:backups')).scalar()
    link = ''
    if not isinstance(value, dict) or not value.get('at'):
        return [] if db.LOCAL else [item('backup', 'Backups', 'warn', 'The backup service has not reported yet. Check with: ./agreements.sh status')]
    out = []
    if now - value['at'] > 3600:
        out.append(item('backup', 'Backups', 'bad', f"The backup service last reported {ago(now - value['at'])} ago. Check with: ./agreements.sh status"))
        return out
    last = value.get('last_backup') or {}
    test = value.get('last_restore_test') or {}
    if not last.get('at'):
        out.append(item('backup', 'Backups', 'warn', 'No backup has finished yet (the first one runs shortly after installation).'))
    elif not last.get('ok'):
        out.append(item('backup', 'Backups', 'bad', f"The last backup ({clock(last['at'])}) failed. See: ./agreements.sh status"))
    elif now - last['at'] > 36 * 3600:
        out.append(item('backup', 'Backups', 'bad', f"The last successful backup is {ago(now - last['at'])} old."))
    elif test.get('at') and not test.get('ok'):
        out.append(item('backup', 'Backups', 'bad', f"The last restore test ({clock(test['at'])}) failed: a backup that cannot be restored is not a backup. See: ./agreements.sh status"))
    else:
        out.append(item('backup', 'Backups', 'ok', f"Last backup {clock(last['at'])}" + (f"; restore test passed {clock(test['at'])}." if test.get('at') else '.')))
    archiving = value.get('archiving') or {}
    if archiving.get('failing'):
        out.append(item('backup:archive', 'Continuous backup', 'bad', f"Database changes are not being copied to the backup ({archiving.get('waiting_mb', 0)} MB waiting on this server). "
                        + ('Check the off-site storage settings (backup-offsite.env).' if value.get('offsite') else 'See: ./agreements.sh logs postgres')))
    if not value.get('offsite'):
        out.append(item('backup:offsite', 'Off-site backup', 'warn', 'Backups are kept on this server only. If its disk fails, the database and its backups are lost together. Set up the off-site copy (START-HERE.md, "Off-site backups").', link))
    elif (value.get('offsite_backup') or {}).get('ok') is False:
        out.append(item('backup:offsite', 'Off-site backup', 'bad', 'The last copy to the off-site storage failed. Check backup-offsite.env and the storage account.'))
    else:
        out.append(item('backup:offsite', 'Off-site backup', 'ok', 'Copied to off-site storage.'))
    return out


def check_disk() -> list[dict]:
    import shutil
    out = []
    for label, path in (('Application data', db.DATA), ('Agreement files', db.OUTPUT)):
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        free = usage.free / usage.total
        if free < 0.15:
            out.append(item('disk', 'Disk space', 'bad' if free < 0.07 else 'warn', f'Only {round(free * 100)}% of the server disk is free ({round(usage.free / 1e9, 1)} GB). A full disk stops the database.'))
            break
    return out


def collect(c, fresh: bool = False) -> list[dict]:
    now = time.time()
    if not fresh and _cache['items'] is not None and now - _cache['at'] < 20:
        return _cache['items']
    items = [item('database', 'Database', 'ok', 'Connected.')]
    for check in (lambda: check_workers(c, now), lambda: check_mail(c, now), lambda: check_queues(c, now), lambda: check_scanner(now), lambda: check_backups(c, now), check_disk):
        try:
            items += check()
        except Exception as exc:  # noqa: BLE001  (a check that cannot run is itself a finding, never an error page)
            items.append(item('check', 'Status check', 'warn', f'One status check could not run ({exc.__class__.__name__}).'))
    _cache.update(at=now, items=items)
    return items


def summary(items: list[dict]) -> str:
    states = {i['state'] for i in items}
    return 'bad' if 'bad' in states else 'warn' if 'warn' in states else 'ok'


def install(app) -> None:
    @app.get('/api/health/deep')
    def api_health_deep(request: Request):
        """For an outside uptime monitor: no sign-in, no details (those are for the Super Admin), 503 when something is broken."""
        with db.engine.connect() as c:
            if not sec.rate_limit_ok_anonymous(c, request):
                raise HTTPException(429, 'Too many requests.')
        with db.engine.connect() as c:
            items = collect(c)
        state = summary(items)
        return JSONResponse({'status': state, 'problems': sum(1 for i in items if i['state'] == 'bad'), 'warnings': sum(1 for i in items if i['state'] == 'warn')},
                            503 if state == 'bad' else 200)

    @app.get('/api/system-status')
    def api_system_status(request: Request, fresh: int = 0):
        with db.engine.connect() as c:
            a = sec.get_actor(request, c)
            if a['role'] != 'super_admin':
                raise HTTPException(403, 'Super Admin access required.')
            items = collect(c, fresh=bool(fresh))
        return {'status': summary(items), 'checked': time.time(), 'items': items}

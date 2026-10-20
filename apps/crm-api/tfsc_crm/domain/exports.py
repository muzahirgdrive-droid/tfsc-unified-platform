"""Approval-logged, row-capped, watermarked, encrypted exports (§21; permission row "Export data").

Manager: request (scope = their own scope). Director/Super Admin: approve or reject, or run directly.
Approval builds a CSV inside an AES-256 ZIP. The ZIP password is random, stored encrypted, and revealed once to the
requester; the file can be downloaded until it expires (24 h). Every step is audited.
The watermark (exporter, time, export id) is the first line of the CSV and part of the file name.
Cells that a spreadsheet would execute (=, +, -, @, tab, CR) are prefixed with an apostrophe (CSV injection).
Meeting fields and Meet links are never exported.
"""
from __future__ import annotations

import csv
import hashlib
import io
import secrets
import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Connection, select

from .. import crypto
from ..db import models as m
from ..ids import uuid7
from ..pacific import utcnow
from ..security.roles import DIRECTOR, Actor, owner_filter
from . import audit

MAX_ROWS = 50_000
TTL = timedelta(hours=24)
KINDS = ('activities', 'leads')
DANGEROUS = ('=', '+', '-', '@', '\t', '\r')


def safe_cell(v: Any) -> str:
    s = '' if v is None else str(v)
    return "'" + s if s.startswith(DANGEROUS) else s


def request(c: Connection, actor: Actor, kind: str, start: date, end: date, reason: str) -> uuid.UUID:
    actor.require('export.request')
    if kind not in KINDS:
        raise HTTPException(422, 'Choose what to export.')
    if start > end or (end - start).days > 400:
        raise HTTPException(422, 'Choose a valid period (up to 400 days).')
    if len(reason.strip()) < 5:
        raise HTTPException(422, 'Give a reason for the export.')
    eid = uuid7()
    c.execute(m.exports.insert().values(id=eid, org_id=actor.org_id, requested_by=actor.id, kind=kind, reason=reason[:1000],
                                        filters={'start': start.isoformat(), 'end': end.isoformat()}, status='requested'))
    audit.record(c, actor.org_id, actor.id, 'export.requested', 'export', eid, None, {'kind': kind, 'start': start.isoformat(), 'end': end.isoformat()})
    return eid


def _rows(c: Connection, requester: Actor, kind: str, start: date, end: date) -> tuple[list[str], list[list[Any]]]:
    if kind == 'activities':
        a, u = m.activities, m.users
        q = (select(a.c.business_date, u.c.display_name, a.c.provider_name, a.c.phone_display, a.c.email, a.c.called, a.c.connected, a.c.text_sent,
                    a.c.email_sent, a.c.score_points, a.c.entry_source, a.c.domain_id, a.c.project_id, a.c.remarks)
             .join(u, u.c.id == a.c.owner_id)
             .where(a.c.org_id == requester.org_id, a.c.business_date.between(start, end), a.c.deleted_at.is_(None), owner_filter(requester, a.c.owner_id))
             .order_by(a.c.business_date, a.c.created_at).limit(MAX_ROWS + 1))
        header = ['date', 'agent', 'provider', 'phone', 'email', 'called', 'connected', 'text_sent', 'email_sent', 'points', 'source',
                  'domain', 'project', 'remarks']
        out: list[list[Any]] = []
        for r in c.execute(q):
            row = list(r)
            row[3], row[4] = crypto.decrypt(row[3]), crypto.decrypt(row[4])
            out.append(row)
        return header, out
    ld, u, act = m.leads, m.users, m.activities
    lq = (select(ld.c.stage, ld.c.qualified_at, ld.c.signed_at, u.c.display_name, act.c.provider_name, act.c.phone_display, ld.c.domain_id, ld.c.project_id,
                ld.c.agreement_status)
         .join(u, u.c.id == ld.c.owner_id)
         .join(act, (act.c.id == ld.c.origin_activity_id) & (act.c.business_date == ld.c.origin_activity_date))
         .where(ld.c.org_id == requester.org_id, ld.c.created_at >= start, ld.c.created_at < end + timedelta(days=1), owner_filter(requester, ld.c.owner_id))
         .order_by(ld.c.created_at).limit(MAX_ROWS + 1))
    header = ['stage', 'qualified_at', 'signed_at', 'owner', 'provider', 'phone', 'domain', 'project', 'agreement_status']
    out2: list[list[Any]] = []
    for lr in c.execute(lq):
        row = list(lr)
        row[5] = crypto.decrypt(row[5])
        out2.append(row)
    return header, out2


def decide(c: Connection, actor: Actor, eid: uuid.UUID, approve: bool, note: str = '') -> str | None:
    """Approve (build the file) or reject. Returns the status."""
    actor.require('export.run')
    e = c.execute(select(m.exports).where(m.exports.c.id == eid).with_for_update()).mappings().first()
    if not e:
        raise HTTPException(404, 'Export not found.')
    if e['status'] != 'requested':
        raise HTTPException(409, 'This export was already decided.')
    if not approve:
        c.execute(m.exports.update().where(m.exports.c.id == eid).values(status='rejected', approved_by=actor.id, approved_at=utcnow(), error=note[:500] or None))
        audit.record(c, actor.org_id, actor.id, 'export.rejected', 'export', eid, None, {'note': note[:200]})
        return 'rejected'
    from ..security.auth import actor_for_user
    requester = actor_for_user(c, e['requested_by'])
    start, end = date.fromisoformat(e['filters']['start']), date.fromisoformat(e['filters']['end'])
    header, rows = _rows(c, requester, e['kind'], start, end)
    if len(rows) > MAX_ROWS:
        c.execute(m.exports.update().where(m.exports.c.id == eid).values(status='rejected', approved_by=actor.id, approved_at=utcnow(),
                                                                         error=f'More than {MAX_ROWS:,} rows. Narrow the period.'))
        audit.record(c, actor.org_id, actor.id, 'export.too_large', 'export', eid, None, {'rows': len(rows)})
        return 'rejected'
    now = utcnow()
    mark = f'Exported for {requester.display_name} · approved by {actor.display_name} · {now:%Y-%m-%d %H:%M} UTC · export {eid}'
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([safe_cell(mark)])
    w.writerow(header)
    for r in rows:
        w.writerow([safe_cell(v) for v in r])
    password = secrets.token_urlsafe(18)
    data = _zip(f'{e["kind"]}-{start}-{end}-{str(eid)[:8]}.csv', buf.getvalue().encode('utf-8-sig'), password)
    key = f'exports/{actor.org_id}/{eid}.zip'
    c.execute(m.stored_files.insert().values(key=key, org_id=actor.org_id, content_type='application/zip', size=len(data),
                                             sha256=hashlib.sha256(data).hexdigest(), data=data))
    c.execute(m.exports.update().where(m.exports.c.id == eid).values(
        status='ready', approved_by=actor.id, approved_at=now, expires_at=now + TTL, row_count=len(rows), object_key=key, watermark=mark[:200],
        file_sha256=hashlib.sha256(data).hexdigest(), password_enc=crypto.encrypt(password)))
    audit.record(c, actor.org_id, actor.id, 'export.approved', 'export', eid, None, {'rows': len(rows), 'sha256': hashlib.sha256(data).hexdigest()})
    return 'ready'


def _zip(name: str, payload: bytes, password: str) -> bytes:
    import pyzipper
    out = io.BytesIO()
    with pyzipper.AESZipFile(out, 'w', compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as z:
        z.setpassword(password.encode())
        z.setencryption(pyzipper.WZ_AES, nbits=256)
        z.writestr(name, payload)
    return out.getvalue()


def _mine(c: Connection, actor: Actor, eid: uuid.UUID) -> dict:
    e = c.execute(select(m.exports).where(m.exports.c.id == eid).with_for_update()).mappings().first()
    if not e or (e['requested_by'] != actor.id and actor.rank < DIRECTOR):
        raise HTTPException(404, 'Export not found.')
    if e['status'] != 'ready' or not e['expires_at'] or e['expires_at'] <= utcnow():
        raise HTTPException(410, 'This export is not available (not approved yet, or expired).')
    return dict(e)


def reveal_password(c: Connection, actor: Actor, eid: uuid.UUID) -> str:
    """The requester sees the ZIP password exactly once."""
    e = _mine(c, actor, eid)
    if e['requested_by'] != actor.id:
        raise HTTPException(403, 'Only the person who requested the export can reveal its password.')
    if e['password_revealed_at']:
        raise HTTPException(409, 'The password was already shown. Ask for a new export if it was lost.')
    c.execute(m.exports.update().where(m.exports.c.id == eid).values(password_revealed_at=utcnow()))
    audit.record(c, actor.org_id, actor.id, 'export.password_revealed', 'export', eid)
    pw = crypto.decrypt(e['password_enc'])
    assert pw
    return pw


def download(c: Connection, actor: Actor, eid: uuid.UUID) -> tuple[str, bytes]:
    e = _mine(c, actor, eid)
    data = c.execute(select(m.stored_files.c.data).where(m.stored_files.c.key == e['object_key'])).scalar_one()
    c.execute(m.exports.update().where(m.exports.c.id == eid).values(downloads=e['downloads'] + 1))
    audit.record(c, actor.org_id, actor.id, 'export.downloaded', 'export', eid, None, {'sha256': e['file_sha256']})
    return f'tfsc-crm-{e["kind"]}-{str(eid)[:8]}.zip', data

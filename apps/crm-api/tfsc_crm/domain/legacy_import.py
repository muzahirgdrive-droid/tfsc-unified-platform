"""Super Admin historical import of the legacy tracking workbook (§25.3, §20.5).

Flow: upload (file stored with its SHA-256) -> scan (sheets, roster, review counts, reconciliation figures)
-> the Super Admin maps each operator sheet to a CRM user (names are not identities) -> import job.

The job is resumable and idempotent: rows are processed in chunks, each chunk commits its activities and its
`import_rows` records together, and a restart skips row numbers that already have a record. Imported rows are
historical: `entry_source = import_legacy`, business date from the sheet, no lock timers, no celebrations, no
notifications and no duplicate flags. Strong FU / Sign Up cells become confirmed milestones attributed to the
importing Super Admin (the workbook had no confirmation step), so scores follow the ladder exactly.
Contradictory and undated rows are not imported; they wait in the review queue with the #REF! rows.
"""
from __future__ import annotations

import logging
import tempfile
import threading
import uuid
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import Connection, func, select

from .. import storage
from ..db import models as m
from ..db.session import read, tx
from ..ids import uuid7
from ..pacific import PACIFIC
from ..security.roles import Actor
from . import activities, audit, duplicates, metrics, rollups, scoring
from . import legacy_workbook as lw

log = logging.getLogger('tfsc_crm.legacy_import')
CHUNK = 400
ROW_KEY = 1_000_000  # import_rows.row_no = sheet index * ROW_KEY + Excel row
MAX_BYTES = 60_000_000
REVIEW_DECISIONS = ('review', 'imported_review')
_running: set[uuid.UUID] = set()
_lock = threading.Lock()


def file_key(org_id: uuid.UUID, sha256: str) -> str:
    return f'imports/legacy/{org_id}/{sha256}.xlsx'


def historical_at(d: date) -> datetime:
    """Workbook rows carry a date but no time: they are stamped at 12:00 Pacific on their business date."""
    return datetime.combine(d, time(12), tzinfo=PACIFIC).astimezone(UTC)


def _load_scan(c: Connection, batch: dict) -> lw.Scan:
    data = storage.get(c, batch['file_object_key'])
    with tempfile.NamedTemporaryFile(suffix='.xlsx') as f:
        f.write(data)
        f.flush()
        s = lw.scan(f.name)
    if s.sha256 != batch['sha256']:
        raise RuntimeError('Stored workbook does not match its recorded SHA-256.')
    return s


def create_batch(c: Connection, actor: Actor, data: bytes, filename: str) -> uuid.UUID:
    import hashlib
    sha = hashlib.sha256(data).hexdigest()
    key = file_key(actor.org_id, sha)
    storage.put(c, actor.org_id, key, data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', replace=False)
    bid = uuid7()
    c.execute(m.import_batches.insert().values(id=bid, org_id=actor.org_id, owner_id=actor.id, created_by=actor.id, kind='legacy',
                                               file_object_key=key, sha256=sha, sheet=filename[:120], status='uploaded',
                                               totals={'filename': filename[:200], 'bytes': len(data)}))
    audit.record(c, actor.org_id, actor.id, 'import.legacy_uploaded', 'import_batch', bid, after={'sha256': sha, 'bytes': len(data)})
    return bid


def _set(c: Connection, bid: uuid.UUID, **vals: Any) -> None:
    c.execute(m.import_batches.update().where(m.import_batches.c.id == bid).values(**vals, updated_at=func.now()))


def run_scan(actor: Actor, bid: uuid.UUID) -> None:
    try:
        with tx(actor.org_id, actor.id) as c:
            batch = dict(c.execute(select(m.import_batches).where(m.import_batches.c.id == bid)).mappings().one())
            _set(c, bid, status='scanning')
        with read(actor.org_id, actor.id) as c:
            s = _load_scan(c, batch)
        start, end = lw.default_period(s)
        summary = s.summary()
        summary['reconciliation'] = lw.reconciliation(s, start, end)
        with tx(actor.org_id, actor.id) as c:
            _set(c, bid, status='scanned', totals={**batch['totals'], 'scan': summary})
    except Exception as e:  # surfaced on the batch; the file stays stored for a retry
        log.exception('legacy scan failed')
        with tx(actor.org_id, actor.id) as c:
            _set(c, bid, status='failed', result={'error': f'Scan failed: {type(e).__name__}'})


def suggest_mapping(c: Connection, org_id: uuid.UUID, sheets: list[str]) -> dict[str, str | None]:
    """Exact (case-insensitive) display-name matches only, as a suggestion the Super Admin must confirm."""
    users = c.execute(select(m.users.c.id, m.users.c.display_name).where(m.users.c.org_id == org_id, m.users.c.crm_role == 'sales_agent')).all()
    by_name: dict[str, list[uuid.UUID]] = {}
    for uid, name in users:
        by_name.setdefault((name or '').strip().lower(), []).append(uid)
    out: dict[str, str | None] = {}
    for sh in sheets:
        hits = by_name.get(sh.strip().lower(), [])
        out[sh] = str(hits[0]) if len(hits) == 1 else None
    return out


def validate_mapping(c: Connection, actor: Actor, sheets: list[str], mapping: dict[str, str]) -> dict[str, uuid.UUID]:
    from fastapi import HTTPException
    out: dict[str, uuid.UUID] = {}
    for sheet, uid in mapping.items():
        if sheet not in sheets:
            raise HTTPException(422, f'Unknown sheet in mapping: {sheet[:60]}')
        if not uid:
            continue
        try:
            u = uuid.UUID(str(uid))
        except ValueError:
            raise HTTPException(422, 'Map each sheet to a user.') from None
        role = c.execute(select(m.users.c.crm_role).where(m.users.c.id == u, m.users.c.org_id == actor.org_id)).scalar_one_or_none()
        if role != 'sales_agent':
            raise HTTPException(422, 'Each sheet must map to a Sales Agent in this organization.')
        out[sheet] = u
    if not out:
        raise HTTPException(422, 'Map at least one sheet to a user.')
    return out


def run_import(actor: Actor, bid: uuid.UUID) -> None:
    with _lock:
        if bid in _running:
            return
        _running.add(bid)
    try:
        _run_import(actor, bid)
    except Exception as e:
        log.exception('legacy import failed')
        with tx(actor.org_id, actor.id) as c:
            _set(c, bid, status='failed', result={'error': f'Import stopped: {type(e).__name__}. Resume to continue from the last saved chunk.'})
    finally:
        with _lock:
            _running.discard(bid)


def _run_import(actor: Actor, bid: uuid.UUID) -> None:
    with read(actor.org_id, actor.id) as c:
        batch = dict(c.execute(select(m.import_batches).where(m.import_batches.c.id == bid)).mappings().one())
        s = _load_scan(c, batch)
        done = {r[0] for r in c.execute(select(m.import_rows.c.row_no).where(m.import_rows.c.batch_id == bid))}
    mapping = {k: uuid.UUID(v) for k, v in batch['mapping'].get('sheets', {}).items() if v}
    order = {name: i for i, name in enumerate(sh['name'] for sh in s.sheets)}
    todo = [r for r in s.rows if r.sheet in mapping and order[r.sheet] * ROW_KEY + r.row_no not in done]
    total = sum(1 for r in s.rows if r.sheet in mapping)
    with tx(actor.org_id, actor.id) as c:
        _set(c, bid, status='importing', result=None, totals={**batch['totals'], 'progress': {'done': total - len(todo), 'total': total}})
    for i in range(0, len(todo), CHUNK):
        chunk = todo[i:i + CHUNK]
        with tx(actor.org_id, actor.id) as c, rollups.batched(c):
            for r in chunk:
                _import_row(c, actor, bid, order[r.sheet] * ROW_KEY + r.row_no, r, mapping[r.sheet])
            _set(c, bid, totals={**batch['totals'], 'progress': {'done': total - len(todo) + i + len(chunk), 'total': total}})
    per = batch['mapping'].get('period') or {}
    start, end = (date.fromisoformat(per['start']), date.fromisoformat(per['end'])) if per else lw.default_period(s)
    with read(actor.org_id, actor.id) as c:
        db = database_figures(c, actor.org_id, list(set(mapping.values())), start, end, bid)
    report = lw.reconciliation(s, start, end, db, sheets=list(mapping))
    with tx(actor.org_id, actor.id) as c:
        _set(c, bid, status='imported', result={'reconciliation': report},
             totals={**batch['totals'], 'progress': {'done': total, 'total': total}})
        audit.record(c, actor.org_id, actor.id, 'import.legacy_completed', 'import_batch', bid,
                     after={'rows': total, 'database_matches_workbook': report.get('database_matches_workbook')})


def _import_row(c: Connection, actor: Actor, bid: uuid.UUID, row_no: int, r: lw.Row, owner: uuid.UUID) -> None:
    raw = {'sheet': r.sheet, 'row': r.row_no, **r.as_input()}
    codes = r.review_codes()
    issues: list[dict] = [{'code': code, 'severity': 'red' if code != 'formula_error' else 'amber',
                           'message': MESSAGES[code], **({'cells': r.ref_cells} if code == 'formula_error' else {})} for code in codes]
    if not r.importable:
        c.execute(m.import_rows.insert().values(org_id=actor.org_id, batch_id=bid, row_no=row_no, raw=raw, issues=issues, decision='review'))
        return
    d = r.business_date
    assert d is not None
    at = historical_at(d)
    res = activities.create(c, actor, r.as_input(), source='import_legacy', owner_id=owner, batch_id=bid, historical=True, at=at)
    a = res.activity
    issues += [{'code': i['code'], 'severity': i['severity'], 'field': i.get('field'), 'message': i['message']}
               for i in res.issues if i.get('severity') == 'red']
    strong, signed = lw.is_yes(r.values[lw.STRONG]), lw.is_yes(r.values[lw.SIGNUP])
    if (strong or signed) and a['called'] is True and a['first_committed_at'] is not None:
        _milestones(c, actor, a, at, strong=strong, signed=signed)
    c.execute(m.import_rows.insert().values(org_id=actor.org_id, batch_id=bid, row_no=row_no, raw=raw, issues=issues,
                                            refined={'business_date': d.isoformat()}, activity_id=a['id'],
                                            decision='imported_review' if issues else 'imported'))


def _milestones(c: Connection, actor: Actor, a: dict, at: datetime, *, strong: bool, signed: bool) -> None:
    note = 'Imported from the legacy workbook (sheet flag; no confirmation step existed).'
    c.execute(m.strong_followups.insert().values(id=uuid7(), org_id=actor.org_id, phone_key=a['phone_key'], activity_id=a['id'],
                                                 activity_date=a['business_date'], requested_by=a['owner_id'], requested_at=at,
                                                 decision='confirmed', decided_by=actor.id, decided_at=at, note=note,
                                                 created_at=at, updated_at=at))
    rollups.bump(c, actor.org_id, a['owner_id'], at, strong_requested=1, strong_confirmed=1)
    state = 'strong_confirmed'
    lead_id = None
    if signed:
        lead_id = uuid7()
        c.execute(m.leads.insert().values(id=lead_id, org_id=actor.org_id, origin_activity_id=a['id'], origin_activity_date=a['business_date'],
                                          phone_key=a['phone_key'], owner_id=a['owner_id'], stage='signed_up', stage_changed_at=at,
                                          qualified_by=actor.id, qualified_at=at, signed_by=actor.id, signed_at=at,
                                          domain_id=a['domain_id'], project_id=a['project_id'], created_at=at, updated_at=at))
        rollups.bump(c, actor.org_id, a['owner_id'], at, leads=1, signups=1)
        state = 'signed'
    duplicates.set_state(c, actor.org_id, a['phone_key'], state, a['owner_id'], lead_id)
    scoring.rescore(c, actor.org_id, a['id'], a['business_date'], at)


MESSAGES = {
    'contradiction': 'An outcome is Yes while Call Attempt is not Yes. Not imported; decide how to record it.',
    'missing_date': 'The row has no valid date. Not imported.',
    'formula_error': 'The workbook score/stage formula on this row is broken (#REF!). Imported from the raw cells; the formula was ignored.',
}


def database_figures(c: Connection, org_id: uuid.UUID, user_ids: list[uuid.UUID], start: date, end: date, bid: uuid.UUID) -> dict[str, Any]:
    """What the CRM now holds for the period (rollups, activity-date credit), for the reconciliation report."""
    s = metrics.aggregate(c, org_id, user_ids, metrics.Period(start, end, 'reconciliation'))
    attempts = s['attempts']
    review: dict[str, int] = dict(c.execute(select(m.import_rows.c.decision, func.count()).where(m.import_rows.c.batch_id == bid)
                            .group_by(m.import_rows.c.decision)).all())
    return {'attempts': attempts, 'connected': s['connected'], 'conn_rate': round(s['connected'] / attempts, 6) if attempts else 0.0,
            'sms': s['sms'], 'emails': s['emails'], 'strong_fu': s['strong_confirmed'], 'signups': s['signups'], 'points': s['points'],
            'quality': round(s['points'] / (100 * attempts), 6) if attempts else 0.0, 'rows_by_decision': review}


def start_thread(fn: Any, *args: Any) -> None:
    from ..config import get_settings
    if get_settings().mode == 'test':
        fn(*args)  # deterministic in tests
        return
    threading.Thread(target=fn, args=args, name=f'legacy-import-{args[-1]}', daemon=True).start()

"""Legacy workbook historical import (Super Admin, §25.3) with the §20.5 reconciliation report."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import Field
from sqlalchemy import select

from ..db import models as m
from ..db.session import read, tx
from ..domain import audit
from ..domain import legacy_import as li
from ..domain import legacy_workbook as lw
from ..pacific import utcnow
from ..security import antivirus
from .deps import ActorDep, parse_uuid
from .schemas import Model, OkOut, Out

router = APIRouter(prefix='/api/admin/legacy-imports', tags=['imports'])


class LegacyBatchOut(Out):
    id: str
    status: str
    filename: str | None
    bytes: int | None
    sha256: str | None
    audited_workbook: bool
    created_at: datetime
    scan: dict[str, Any] | None = None
    mapping: dict[str, str | None] = Field(default_factory=dict)
    suggested_mapping: dict[str, str | None] = Field(default_factory=dict)
    progress: dict[str, int] | None = None
    result: dict[str, Any] | None = None


class StartIn(Model):
    mapping: dict[str, str | None] = Field(max_length=200)
    period_start: date | None = None  # reconciliation period; default: 1–14 Oct 2026 for the audited workbook, else its date span
    period_end: date | None = None


class ReviewItemOut(Out):
    row_no: int
    sheet: str
    row: int
    decision: str
    issues: list[dict[str, Any]]
    cells: dict[str, Any]
    activity_id: str | None


class ReviewPageOut(Out):
    items: list[ReviewItemOut]
    next_cursor: int | None
    counts: dict[str, int]


class ReviewDecisionIn(Model):
    action: Literal['dismiss', 'acknowledge']
    note: str = Field(default='', max_length=1000)


def _batch(c, a, bid) -> dict:
    b = c.execute(select(m.import_batches).where(m.import_batches.c.id == bid, m.import_batches.c.kind == 'legacy')).mappings().first()
    if not b:
        raise HTTPException(404, 'Import not found.')
    return dict(b)


def _out(c, a, b: dict) -> LegacyBatchOut:
    totals = b['totals'] or {}
    scan = totals.get('scan')
    sheets = [s['name'] for s in (scan or {}).get('sheets', []) if not s.get('skipped')]
    return LegacyBatchOut(
        id=str(b['id']), status=b['status'], filename=totals.get('filename'), bytes=totals.get('bytes'), sha256=b['sha256'],
        audited_workbook=b['sha256'] == lw.AUDITED_SHA256, created_at=b['created_at'], scan=scan,
        mapping=(b['mapping'] or {}).get('sheets', {}), suggested_mapping=li.suggest_mapping(c, a.org_id, sheets) if sheets else {},
        progress=totals.get('progress'), result=b['result'])


@router.post('', response_model=LegacyBatchOut, status_code=201)
async def upload(a: ActorDep, file: UploadFile | None = File(None)) -> LegacyBatchOut:
    a.require('imports.legacy')  # before any body handling: other roles always get 403
    if file is None:
        raise HTTPException(422, 'Choose the .xlsx workbook to upload.')
    data = await file.read(li.MAX_BYTES + 1)
    if len(data) > li.MAX_BYTES:
        raise HTTPException(413, 'Use a workbook smaller than 60 MB.')
    if not data.startswith(b'PK'):
        raise HTTPException(400, 'Upload the .xlsx workbook.')
    await antivirus.check_upload(a, data, file.filename or 'workbook.xlsx')
    with tx(a.org_id, a.id) as c:
        bid = li.create_batch(c, a, data, file.filename or 'workbook.xlsx')
    li.start_thread(li.run_scan, a, bid)
    with read(a.org_id, a.id) as c:
        return _out(c, a, _batch(c, a, bid))


@router.get('', response_model=list[LegacyBatchOut])
def list_batches(a: ActorDep) -> list[LegacyBatchOut]:
    a.require('imports.legacy')
    with read(a.org_id, a.id) as c:
        rows = c.execute(select(m.import_batches).where(m.import_batches.c.kind == 'legacy').order_by(m.import_batches.c.created_at.desc())
                         .limit(50)).mappings().all()
        return [_out(c, a, dict(r)) for r in rows]


@router.get('/{batch_id}', response_model=LegacyBatchOut)
def get_batch(batch_id: str, a: ActorDep) -> LegacyBatchOut:
    a.require('imports.legacy')
    with read(a.org_id, a.id) as c:
        return _out(c, a, _batch(c, a, parse_uuid(batch_id)))


@router.post('/{batch_id}/start', response_model=LegacyBatchOut)
def start(batch_id: str, body: StartIn, a: ActorDep) -> LegacyBatchOut:
    """Start (or resume) the import with a confirmed sheet → user mapping."""
    a.require('imports.legacy')
    bid = parse_uuid(batch_id)
    with tx(a.org_id, a.id) as c:
        b = _batch(c, a, bid)
        if b['status'] == 'imported':
            raise HTTPException(409, 'This workbook has already been imported.')
        if b['status'] == 'importing' and bid in li._running:
            raise HTTPException(409, 'The import is already running.')
        scan = (b['totals'] or {}).get('scan')
        if not scan:
            raise HTTPException(409, 'Wait for the scan to finish.')
        sheets = [s['name'] for s in scan['sheets'] if not s.get('skipped')]
        if b['mapping'].get('sheets') and b['status'] in ('importing', 'failed'):
            stored = b['mapping']  # resume: mapping and period are fixed once rows were written
        else:
            mapping = {k: str(v) for k, v in li.validate_mapping(c, a, sheets, {k: v for k, v in body.mapping.items() if v}).items()}
            stored = {'sheets': mapping}
            if body.period_start or body.period_end:
                if not (body.period_start and body.period_end and body.period_start <= body.period_end):
                    raise HTTPException(422, 'Choose a valid reconciliation period.')
                stored['period'] = {'start': body.period_start.isoformat(), 'end': body.period_end.isoformat()}
        mapping = stored['sheets']
        c.execute(m.import_batches.update().where(m.import_batches.c.id == bid)
                  .values(mapping=stored, status='importing', updated_at=utcnow()))
        audit.record(c, a.org_id, a.id, 'import.legacy_started', 'import_batch', bid, after={'sheets': len(mapping)})
    li.start_thread(li.run_import, a, bid)
    with read(a.org_id, a.id) as c:
        return _out(c, a, _batch(c, a, bid))


@router.get('/{batch_id}/review', response_model=ReviewPageOut)
def review(batch_id: str, a: ActorDep, cursor: int | None = None, limit: int = 50) -> ReviewPageOut:
    a.require('imports.legacy')
    bid = parse_uuid(batch_id)
    limit = max(1, min(limit, 200))
    t = m.import_rows
    with read(a.org_id, a.id) as c:
        _batch(c, a, bid)
        q = select(t).where(t.c.batch_id == bid, t.c.decision.in_(li.REVIEW_DECISIONS))
        if cursor is not None:
            q = q.where(t.c.row_no > cursor)
        rows = c.execute(q.order_by(t.c.row_no).limit(limit + 1)).mappings().all()
        from sqlalchemy import func
        counts: dict[str, int] = dict(c.execute(select(t.c.decision, func.count()).where(t.c.batch_id == bid).group_by(t.c.decision)).all())
    items = [ReviewItemOut(row_no=r['row_no'], sheet=r['raw'].get('sheet', ''), row=r['raw'].get('row', 0), decision=r['decision'],
                           issues=r['issues'], cells={k: v for k, v in r['raw'].items() if k not in ('sheet', 'row')},
                           activity_id=str(r['activity_id']) if r['activity_id'] else None) for r in rows[:limit]]
    return ReviewPageOut(items=items, next_cursor=rows[limit - 1]['row_no'] if len(rows) > limit else None, counts=counts)


@router.post('/{batch_id}/review/{row_no}', response_model=OkOut)
def decide(batch_id: str, row_no: str, body: ReviewDecisionIn, a: ActorDep) -> OkOut:
    """Close a review item. Contradictory/undated rows are dismissed (re-enter them manually if they should count);
    imported rows with a broken formula or a data-quality issue are acknowledged."""
    a.require('imports.legacy')
    bid = parse_uuid(batch_id)
    if not row_no.isdigit():
        raise HTTPException(404, 'Review item not found.')
    n = int(row_no)
    t = m.import_rows
    with tx(a.org_id, a.id) as c:
        r = c.execute(select(t).where(t.c.batch_id == bid, t.c.row_no == n).with_for_update()).mappings().first()
        if not r or r['decision'] not in li.REVIEW_DECISIONS:
            raise HTTPException(404, 'Review item not found.')
        want = 'dismiss' if r['decision'] == 'review' else 'acknowledge'
        if body.action != want:
            raise HTTPException(422, 'Dismiss rows that were not imported; acknowledge rows that were imported.')
        new = 'dismissed' if body.action == 'dismiss' else 'acknowledged'
        c.execute(t.update().where(t.c.batch_id == bid, t.c.row_no == n).values(decision=new))
        audit.record(c, a.org_id, a.id, f'import.review_{new}', 'import_row', f'{bid}:{n}', after={'note': body.note[:200]})
    return OkOut()

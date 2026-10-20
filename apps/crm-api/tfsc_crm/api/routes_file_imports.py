"""XLSX/CSV import job (§11.2): upload, choose a sheet and column mapping, run, read per-row results."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import Field
from sqlalchemy import select

from ..db import models as m
from ..db.session import read, tx
from ..domain import audit
from ..domain import file_import as fi
from ..pacific import utcnow
from ..security import ratelimit
from ..security.roles import SUPERVISOR, in_scope
from .deps import ActorDep, parse_uuid
from .schemas import Model, Out

router = APIRouter(prefix='/api/imports', tags=['imports'])
ColumnKey = Literal['business_date', 'name', 'phone', 'email', 'called', 'connected', 'text_sent', 'email_sent', 'strong_followup',
                    'sign_up', 'followup_count', 'last_followup', 'next_followup', 'call_again', 'remarks']


class SheetOut(Out):
    name: str
    rows: int
    too_many: bool
    header_detected: bool
    mapping: list[str | None]
    headers: list[str]


class ImportOut(Out):
    id: str
    status: str
    filename: str | None
    sha256: str | None
    created_at: datetime
    sheets: list[SheetOut]
    sheet: str | None
    progress: dict[str, int] | None = None
    result: dict[str, Any] | None = None


class ImportStartIn(Model):
    sheet: str = Field(min_length=1, max_length=120)
    columns: list[ColumnKey | None] | None = Field(default=None, max_length=40)  # None: use the detected/positional mapping
    header: bool | None = None  # first row is a header (default: as detected)
    owner_id: uuid.UUID | None = None  # Supervisor+ may import for someone in scope


class ImportRowOut(Out):
    row_no: int
    decision: str
    issues: list[dict[str, Any]]
    activity_id: str | None


class ImportRowsOut(Out):
    items: list[ImportRowOut]
    next_cursor: int | None


def _get(c, a, bid: uuid.UUID) -> dict:
    b = c.execute(select(m.import_batches).where(m.import_batches.c.id == bid, m.import_batches.c.kind == 'file',
                                                 m.import_batches.c.created_by == a.id)).mappings().first()
    if not b:
        raise HTTPException(404, 'Import not found.')
    return dict(b)


def _out(b: dict) -> ImportOut:
    t = b['totals'] or {}
    return ImportOut(id=str(b['id']), status=b['status'], filename=t.get('filename'), sha256=b['sha256'], created_at=b['created_at'],
                     sheets=[SheetOut(**s) for s in t.get('sheets', [])], sheet=b['sheet'], progress=t.get('progress'), result=b['result'])


@router.post('', response_model=ImportOut, status_code=201)
async def upload(a: ActorDep, file: UploadFile | None = File(None)) -> ImportOut:
    a.require('activities.own')
    if file is None:
        raise HTTPException(422, 'Choose an .xlsx or .csv file.')
    ratelimit.enforce(f'import-upload:{a.id}', 20, 3600, 'Too many uploads. Please wait a while.')
    data = await file.read(fi.MAX_BYTES + 1)
    if len(data) > fi.MAX_BYTES:
        raise HTTPException(413, 'Use a file smaller than 20 MB (split larger workbooks).')
    name = (file.filename or 'upload').strip()
    if not name.lower().endswith(('.xlsx', '.csv', '.tsv', '.txt')):
        raise HTTPException(400, 'Upload an .xlsx, .csv or .tsv file.')
    with tx(a.org_id, a.id) as c:
        bid = fi.create_upload(c, a, data, name)
        return _out(_get(c, a, bid))


@router.get('', response_model=list[ImportOut])
def mine(a: ActorDep) -> list[ImportOut]:
    with read(a.org_id, a.id) as c:
        rows = c.execute(select(m.import_batches).where(m.import_batches.c.kind == 'file', m.import_batches.c.created_by == a.id)
                         .order_by(m.import_batches.c.created_at.desc()).limit(20)).mappings().all()
    return [_out(dict(r)) for r in rows]


@router.get('/{import_id}', response_model=ImportOut)
def get_import(import_id: str, a: ActorDep) -> ImportOut:
    with read(a.org_id, a.id) as c:
        return _out(_get(c, a, parse_uuid(import_id)))


@router.post('/{import_id}/start', response_model=ImportOut)
def start(import_id: str, body: ImportStartIn, a: ActorDep) -> ImportOut:
    """Start or resume. Re-importing the same file + sheet returns the earlier job instead of saving twice."""
    bid = parse_uuid(import_id)
    with tx(a.org_id, a.id) as c:
        b = _get(c, a, bid)
        if b['status'] == 'done':
            raise HTTPException(409, 'This import has finished.')
        if b['status'] == 'importing' and bid in fi._running:
            raise HTTPException(409, 'The import is already running.')
        if b['status'] in ('importing', 'failed') and b['mapping']:
            pass  # resume with the stored sheet/mapping
        else:
            sheet = next((s for s in b['totals'].get('sheets', []) if s['name'] == body.sheet), None)
            if not sheet:
                raise HTTPException(422, 'Choose one of the sheets in the file.')
            if sheet['too_many']:
                raise HTTPException(422, f'A job holds at most {fi.MAX_ROWS:,} rows. Split the sheet.')
            columns = list(body.columns) if body.columns is not None else sheet['mapping']
            if not any(k in ('name', 'phone') for k in columns):
                raise HTTPException(422, 'Map at least the provider name or phone column.')
            if body.owner_id and body.owner_id != a.id and (a.rank < SUPERVISOR or not in_scope(c, a, body.owner_id)):
                raise HTTPException(403, 'You can only import for yourself or people in your scope.')
            key = f'file:{b["sha256"][:40]}:{body.sheet}'[:80]
            dup = c.execute(select(m.import_batches.c.id).where(m.import_batches.c.owner_id == (body.owner_id or a.id),
                                                                m.import_batches.c.request_key == key)).scalar_one_or_none()
            if dup:
                raise HTTPException(409, f'This sheet of this file was already imported (import {dup}).')
            header = sheet['header_detected'] if body.header is None else body.header
            c.execute(m.import_batches.update().where(m.import_batches.c.id == bid).values(
                sheet=body.sheet, request_key=key, owner_id=body.owner_id or a.id,
                mapping={'columns': columns, 'header': header, 'owner_id': str(body.owner_id) if body.owner_id else None}))
        c.execute(m.import_batches.update().where(m.import_batches.c.id == bid).values(status='importing', updated_at=utcnow()))
        audit.record(c, a.org_id, a.id, 'import.file_started', 'import_batch', bid, after={'sheet': body.sheet[:60]})
    fi.start_thread(a, bid)
    with read(a.org_id, a.id) as c:
        return _out(_get(c, a, bid))


@router.get('/{import_id}/rows', response_model=ImportRowsOut)
def rows(import_id: str, a: ActorDep, decision: Literal['saved', 'rejected', 'review'] | None = None, cursor: int | None = None,
         limit: int = 100) -> ImportRowsOut:
    bid = parse_uuid(import_id)
    limit = max(1, min(limit, 500))
    t = m.import_rows
    with read(a.org_id, a.id) as c:
        _get(c, a, bid)
        q = select(t.c.row_no, t.c.decision, t.c.issues, t.c.activity_id).where(t.c.batch_id == bid)
        if decision:
            q = q.where(t.c.decision == decision)
        if cursor is not None:
            q = q.where(t.c.row_no > cursor)
        found = c.execute(q.order_by(t.c.row_no).limit(limit + 1)).mappings().all()
    items = [ImportRowOut(row_no=r['row_no'], decision=r['decision'], issues=r['issues'], activity_id=str(r['activity_id']) if r['activity_id'] else None)
             for r in found[:limit]]
    return ImportRowsOut(items=items, next_cursor=found[limit - 1]['row_no'] if len(found) > limit else None)

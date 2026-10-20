"""XLSX/CSV import job (§11.2): large uploads are processed on the server, resumable, with per-row results.

Same semantics as Sheet View's Refine & Save with source = import: the server refines every row, saves the
valid ones (live rules: Pacific business date at commit, the admin-set import lock window, duplicate checks
and flags), rejects invalid rows with reasons, and routes contradictions to the review queue.
Limits: 50,000 rows per job, 20 MB per file. The original file and its SHA-256 are stored. Re-importing the same
file + sheet returns the existing job (idempotent); a stopped job resumes after its last committed chunk.
Spreadsheet content is data only; nothing in a cell is ever interpreted as an instruction.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import threading
import uuid
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Connection, func, select

from ..db import models as m
from ..db.session import read, tx
from ..ids import uuid7
from ..security.roles import Actor
from . import activities, duplicates, rollups

log = logging.getLogger('tfsc_crm.file_import')
MAX_ROWS = 50_000
MAX_BYTES = 20_000_000
CHUNK = 250
_running: set[uuid.UUID] = set()
_lock = threading.Lock()

# Header aliases — identical to Sheet View's column model (apps/crm-web/src/lib/sheet.ts).
ALIASES: dict[str, tuple[str, ...]] = {
    'business_date': ('date', 'business date'),
    'name': ('provider name', 'provider', 'name', 'practice', 'practice name'),
    'phone': ('phone', 'phone number', 'number', 'contact'),
    'email': ('email', 'e-mail', 'email address'),
    'called': ('call attempt', 'called', 'attempt'),
    'connected': ('connected', 'connect'),
    'text_sent': ('text sent', 'text', 'sms'),
    'email_sent': ('email sent',),
    'strong_followup': ('strong follow-up', 'strong follow up', 'strong fu', 'strong'),
    'sign_up': ('sign up', 'signup', 'signed'),
    'followup_count': ('# follow-ups', '# follow ups', 'follow-ups', 'followups'),
    'last_followup': ('last follow-up', 'last follow up', 'last followup'),
    'next_followup': ('next follow-up', 'next follow up', 'next followup'),
    'call_again': ('call again?', 'call again'),
    'remarks': ('remarks', 'notes', 'comment', 'comments'),
}
KEYS = tuple(ALIASES)


def _norm(v: Any) -> str:
    return ' '.join(str(v or '').strip().lower().split())


def map_header(first: list[Any]) -> list[str | None] | None:
    """Column keys for a header row, or None when fewer than two headers are recognised (no header row)."""
    out = [next((k for k, al in ALIASES.items() if _norm(h) in al), None) for h in first]
    return out if sum(1 for k in out if k) >= 2 else None


def _cell(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v)
    return s if s.strip() else None


def read_tables(data: bytes, filename: str) -> dict[str, list[list[str | None]]]:
    """Every sheet's rows as text cells (at most MAX_ROWS + 2 rows each, so oversize files are detectable)."""
    name = filename.lower()
    if name.endswith('.xlsx') or data.startswith(b'PK'):
        import openpyxl  # defusedxml installed: XML parsed defensively
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:
            raise HTTPException(400, 'This file could not be read as an Excel workbook.') from None
        try:
            out: dict[str, list[list[str | None]]] = {}
            for ws in wb.worksheets:
                if getattr(ws, 'sheet_state', 'visible') != 'visible':
                    continue
                rows: list[list[str | None]] = []
                for r in ws.iter_rows(values_only=True, max_col=40):
                    cells = [_cell(v) for v in r]
                    if any(cells):
                        rows.append(cells)
                    if len(rows) > MAX_ROWS + 1:
                        break
                out[ws.title[:120]] = rows
            return out
        finally:
            wb.close()
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = data.decode('cp1252', errors='replace')
    sample = text[:20000]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=',\t;')
    except csv.Error:
        dialect = csv.excel_tab if '\t' in sample else csv.excel
    rows = []
    for r in csv.reader(io.StringIO(text), dialect):
        cells = [c if c.strip() else None for c in r[:40]]
        if any(cells):
            rows.append(cells)
        if len(rows) > MAX_ROWS + 1:
            break
    return {'CSV': rows}


def summarize(tables: dict[str, list[list[str | None]]]) -> list[dict]:
    out = []
    for name, rows in tables.items():
        header = map_header(rows[0]) if rows else None
        body = len(rows) - (1 if header else 0)
        out.append({'name': name, 'rows': min(body, MAX_ROWS + 1), 'too_many': body > MAX_ROWS, 'header_detected': header is not None,
                    'mapping': header or list(KEYS[:len(rows[0])] if rows else []), 'headers': [str(h or '')[:60] for h in (rows[0] if rows else [])][:40]})
    return out


def to_row(cells: list[str | None], mapping: list[str | None]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for v, k in zip(cells, mapping, strict=False):
        if k and v is not None:
            row[k] = v
    return row


def save_rows(c: Connection, actor: Actor, batch_id: uuid.UUID, rows: list[tuple[int, dict]], source: str,
              owner_id: uuid.UUID | None = None) -> list[tuple[dict, dict]]:
    """Save many rows with staged rollups. Fast path: no per-row savepoint (every rejection in activities.create
    happens before its first write). If anything unexpected fails, the fast path is rolled back as a whole and the rows
    are saved again one savepoint each, so one bad row can never take the others down."""
    duplicates.lock_registries(c, actor.org_id, [r.get('phone') for _, r in rows])  # outside the savepoint: kept on fallback
    try:
        with c.begin_nested(), rollups.batched(c):
            return [process_row(c, actor, batch_id, n, r, source, owner_id, savepoint=False) for n, r in rows]
    except Exception:
        log.warning('batch %s: a row failed after writing; saving row by row', batch_id, exc_info=True)
    with rollups.batched(c):
        return [process_row(c, actor, batch_id, n, r, source, owner_id) for n, r in rows]


def process_row(c: Connection, actor: Actor, batch_id: uuid.UUID, row_no: int, row: dict, source: str,
                owner_id: uuid.UUID | None = None, *, savepoint: bool = True) -> tuple[dict, dict]:
    """Save one row (inside its own savepoint unless the caller handles failures). Returns (result, import_rows record)."""
    try:
        if savepoint:
            with rollups.row(c), c.begin_nested():
                res = activities.create(c, actor, row, source=source, owner_id=owner_id, batch_id=batch_id)
        else:
            c.info['row_wrote'] = False
            try:
                with rollups.row(c):
                    res = activities.create(c, actor, row, source=source, owner_id=owner_id, batch_id=batch_id)
            except (activities.RowRejected, HTTPException):
                if c.info.get('row_wrote'):  # rejected after a write: only a savepoint can undo it -> slow path
                    raise RuntimeError('row rejected after writing') from None
                raise
        result = {'row_no': row_no, 'status': 'saved', 'activity_id': res.activity['id'], 'issues': res.issues,
                  'duplicate_notice': res.duplicate.notice if res.duplicate.severity else None}
    except activities.RowRejected as e:
        review = e.kind == 'contradiction' and source == 'import'
        result = {'row_no': row_no, 'status': 'review' if review else 'rejected', 'activity_id': None, 'issues': e.issues}
        if review:
            c.execute(m.flags.insert().values(id=uuid7(), org_id=actor.org_id, severity='amber', kind='import_contradiction',
                                              raised_for_user=actor.id, detail={'batch_id': str(batch_id), 'row_no': row_no, 'issues': e.issues}))
    except HTTPException as e:
        result = {'row_no': row_no, 'status': 'rejected', 'activity_id': None,
                  'issues': [{'field': 'row', 'severity': 'red', 'code': 'rejected', 'message': str(e.detail)}]}
    record = {'org_id': actor.org_id, 'batch_id': batch_id, 'row_no': row_no, 'raw': jsonable(row), 'issues': result['issues'],
              'decision': result['status'], 'activity_id': result['activity_id']}
    return result, record


def jsonable(row: dict) -> dict:
    return {str(k): (v if isinstance(v, (str, int, float, bool)) or v is None else str(v)) for k, v in row.items()}


def create_upload(c: Connection, actor: Actor, data: bytes, filename: str) -> uuid.UUID:
    from sqlalchemy.dialects.postgresql import insert
    tables = read_tables(data, filename)
    if not any(tables.values()):
        raise HTTPException(400, 'The file has no rows.')
    sha = hashlib.sha256(data).hexdigest()
    ext = 'xlsx' if data.startswith(b'PK') else 'csv'
    key = f'imports/file/{actor.org_id}/{sha}.{ext}'
    c.execute(insert(m.stored_files).values(key=key, org_id=actor.org_id, content_type='text/csv' if ext == 'csv' else
                                            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                                            size=len(data), sha256=sha, data=data).on_conflict_do_nothing())
    bid = uuid7()
    c.execute(m.import_batches.insert().values(id=bid, org_id=actor.org_id, owner_id=actor.id, created_by=actor.id, kind='file',
                                               file_object_key=key, sha256=sha, status='uploaded',
                                               totals={'filename': filename[:200], 'bytes': len(data), 'sheets': summarize(tables)}))
    return bid


def run(actor: Actor, bid: uuid.UUID) -> None:
    with _lock:
        if bid in _running:
            return
        _running.add(bid)
    try:
        _run(actor, bid)
    except Exception as e:
        log.exception('file import failed')
        with tx(actor.org_id, actor.id) as c:
            _set(c, bid, status='failed', result={'error': f'Import stopped: {type(e).__name__}. Resume to continue.'})
    finally:
        with _lock:
            _running.discard(bid)


def _set(c: Connection, bid: uuid.UUID, **vals: Any) -> None:
    c.execute(m.import_batches.update().where(m.import_batches.c.id == bid).values(**vals, updated_at=func.now()))


def _run(actor: Actor, bid: uuid.UUID) -> None:
    with read(actor.org_id, actor.id) as c:
        b = dict(c.execute(select(m.import_batches).where(m.import_batches.c.id == bid)).mappings().one())
        data = c.execute(select(m.stored_files.c.data).where(m.stored_files.c.key == b['file_object_key'])).scalar_one()
        done = {r[0] for r in c.execute(select(m.import_rows.c.row_no).where(m.import_rows.c.batch_id == bid))}
    cfg = b['mapping']
    rows = read_tables(data, b['totals'].get('filename', ''))[b['sheet']]
    if cfg.get('header'):
        rows = rows[1:]
    rows = rows[:MAX_ROWS]
    owner = uuid.UUID(cfg['owner_id']) if cfg.get('owner_id') else None
    total = len(rows)
    todo = [i for i in range(total) if i not in done]
    for start in range(0, len(todo), CHUNK):
        part = todo[start:start + CHUNK]
        with tx(actor.org_id, actor.id) as c:
            records = [rec for _res, rec in save_rows(c, actor, bid, [(i, to_row(rows[i], cfg['columns'])) for i in part], 'import', owner)]
            c.execute(m.import_rows.insert(), records)
            _set(c, bid, totals={**b['totals'], 'progress': {'done': total - len(todo) + start + len(part), 'total': total}})
    with tx(actor.org_id, actor.id) as c:
        counts: dict[str, int] = dict(c.execute(select(m.import_rows.c.decision, func.count()).where(m.import_rows.c.batch_id == bid)
                                .group_by(m.import_rows.c.decision)).all())
        _set(c, bid, status='done', result={'saved': counts.get('saved', 0), 'rejected': counts.get('rejected', 0), 'review': counts.get('review', 0)},
             totals={**b['totals'], 'progress': {'done': total, 'total': total}})


def start_thread(actor: Actor, bid: uuid.UUID) -> None:
    from ..config import get_settings
    if get_settings().mode == 'test':
        run(actor, bid)
        return
    threading.Thread(target=run, args=(actor, bid), name=f'file-import-{bid}', daemon=True).start()

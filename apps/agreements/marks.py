"""Test agreements: the test / real marks on agreements (contracts), envelopes and clients.

A test item is a practice run. Its documents carry a TEST watermark, its emails start with "[TEST]", and it stays out of
Home, Insights, reports and client counts unless a report asks to include it. Marks can be changed later, one item at a
time or in bulk, by the people who may edit the item (its owner, their Admin, a Super Admin). Every change is logged.

Client rule: a client created by a test generation is a test client. A real agreement or a real envelope for a client makes
it a real client. When items are marked, the client follows them: it is a test client while every agreement and envelope it
has is a test, and a real client as soon as one of them is real.
"""
from __future__ import annotations

import io

from fastapi import HTTPException
from sqlalchemy import and_, select, update

import database as db
import security as sec
from document_engine import TEST_WATERMARK
from esign_db import envelopes

MAX_BULK = 200


def ids_of(payload: dict) -> list[str]:
    raw = payload.get('ids')
    if raw is None and payload.get('id'):
        raw = [payload['id']]
    if not isinstance(raw, list) or not raw or any(not isinstance(x, str) or not x or len(x) > 64 for x in raw):
        raise HTTPException(400, 'Choose at least one item to mark.')
    if len(raw) > MAX_BULK:
        raise HTTPException(400, f'Mark up to {MAX_BULK} items at a time.')
    return list(dict.fromkeys(raw))


def flag_of(payload: dict) -> bool:
    value = payload.get('test')
    if not isinstance(value, bool):
        raise HTTPException(400, 'Say whether to mark the items as test (true) or real (false).')
    return value


# ------------------------------------------------------------------ the client rule
def client_items(c, client_id: str) -> tuple[int, int]:
    """(real, test) counts of a client's agreements and sent envelopes (templates and unsent drafts are not items yet)."""
    real = test = 0
    for flag in c.execute(select(db.contracts.c.test).where(db.contracts.c.client_id == client_id)).scalars():
        real, test = (real, test + 1) if flag else (real + 1, test)
    for flag in c.execute(select(envelopes.c.test).where(envelopes.c.client_id == client_id, envelopes.c.status.notin_(('template', 'draft')))).scalars():
        real, test = (real, test + 1) if flag else (real + 1, test)
    return real, test


def set_client(c, actor, client_id: str, test: bool, why: str) -> bool:
    row = c.execute(select(db.clients.c.test, db.clients.c.name).where(db.clients.c.id == client_id)).first()
    if not row or bool(row[0]) == test:
        return False
    c.execute(update(db.clients).where(db.clients.c.id == client_id).values(test=test))
    db.log(c, actor, 'client.marked_test' if test else 'client.marked_real', client_id, {'client': row[1], 'reason': why})
    return True


def sync_client(c, actor, client_id: str | None, why: str) -> None:
    """The client follows its items: real if any item is real; test if it has items and all are tests."""
    if not client_id:
        return
    real, test = client_items(c, client_id)
    if real:
        set_client(c, actor, client_id, False, why)
    elif test:
        set_client(c, actor, client_id, True, why)


def real_activity(c, actor, client_id: str | None, why: str) -> None:
    """A real agreement or a real envelope makes its client a real client."""
    if client_id:
        set_client(c, actor, client_id, False, why)


# ------------------------------------------------------------------ marking
def mark_contracts(c, actor: dict, ids: list[str], test: bool) -> int:
    rows = c.execute(select(db.contracts.c.id, db.contracts.c.client_id, db.contracts.c.test).where(
        db.contracts.c.id.in_(ids), sec.scope(actor, db.contracts.c.owner_id), sec.company_scope(actor, db.contracts.c.entity))).mappings().all()
    if len(rows) != len(ids):
        raise HTTPException(404, 'One or more agreements were not found, or you cannot edit them.')
    changed = 0
    for r in rows:
        if bool(r['test']) != test:
            c.execute(update(db.contracts).where(db.contracts.c.id == r['id']).values(test=test))
            db.log(c, actor, 'contract.marked_test' if test else 'contract.marked_real', r['client_id'], {'contract_id': r['id']})
            changed += 1
        # the envelopes sent from this agreement are the same agreement
        sent = c.execute(select(envelopes.c.id).where(envelopes.c.contract_id == r['id'], envelopes.c.status != 'template', envelopes.c.test != test,
                                                      _envelope_scope(actor))).scalars().all()
        if sent:
            _mark_envelope_rows(c, actor, sent, test)
    for client_id in {r['client_id'] for r in rows}:
        sync_client(c, actor, client_id, 'agreement marked as ' + ('test' if test else 'real'))
    return changed


def _envelope_scope(actor):
    return and_(sec.scope(actor, envelopes.c.owner_id), sec.company_scope(actor, envelopes.c.entity))


def _mark_envelope_rows(c, actor: dict, ids: list[str], test: bool) -> list[dict]:
    import esign
    rows = c.execute(select(envelopes.c.id, envelopes.c.ref, envelopes.c.client_id, envelopes.c.contract_id, envelopes.c.subject, envelopes.c.test)
                     .where(envelopes.c.id.in_(ids))).mappings().all()
    changed = []
    for r in rows:
        if bool(r['test']) == test:
            continue
        # The version is left alone so an editor open on a draft can still save.
        c.execute(update(envelopes).where(envelopes.c.id == r['id']).values(test=test))
        esign.record(c, r['id'], 'marked_test' if test else 'marked_real', actor['name'])
        db.log(c, actor, 'esign.marked_test' if test else 'esign.marked_real', r['client_id'], {'envelope': r['ref'], 'subject': r['subject']})
        changed.append(dict(r))
    return changed


def mark_envelopes(c, actor: dict, ids: list[str], test: bool) -> int:
    found = c.execute(select(envelopes.c.id).where(envelopes.c.id.in_(ids), _envelope_scope(actor))).scalars().all()
    if len(found) != len(ids):
        raise HTTPException(404, 'One or more envelopes were not found, or you cannot edit them.')
    changed = _mark_envelope_rows(c, actor, ids, test)
    # An agreement follows its envelopes when every envelope sent from it now has the same mark.
    for contract_id in {r['contract_id'] for r in changed if r['contract_id']}:
        flags = set(c.execute(select(envelopes.c.test).where(envelopes.c.contract_id == contract_id, envelopes.c.status != 'template')).scalars())
        if flags == {test}:
            row = c.execute(select(db.contracts.c.test, db.contracts.c.client_id).where(db.contracts.c.id == contract_id)).first()
            if row and bool(row[0]) != test:
                c.execute(update(db.contracts).where(db.contracts.c.id == contract_id).values(test=test))
                db.log(c, actor, 'contract.marked_test' if test else 'contract.marked_real', row[1], {'contract_id': contract_id, 'reason': 'its envelopes were marked'})
    for client_id in {r['client_id'] for r in changed if r['client_id']}:
        sync_client(c, actor, client_id, 'envelope marked as ' + ('test' if test else 'real'))
    return len(changed)


def mark_client(c, actor: dict, client_id: str, test: bool) -> dict:
    """Marking a client as a test also marks its agreements and envelopes; marking it real changes only the client."""
    row = c.execute(select(db.clients.c.id).where(db.clients.c.id == client_id, sec.scope(actor, db.clients.c.owner_id))).first()
    if not row:
        raise HTTPException(404, 'Client not found.')
    contracts, sent = 0, 0
    if test:
        ids = c.execute(select(db.contracts.c.id).where(db.contracts.c.client_id == client_id, db.contracts.c.test.is_(False),
                                                        sec.scope(actor, db.contracts.c.owner_id), sec.company_scope(actor, db.contracts.c.entity))).scalars().all()
        for contract_id in ids:
            c.execute(update(db.contracts).where(db.contracts.c.id == contract_id).values(test=True))
            db.log(c, actor, 'contract.marked_test', client_id, {'contract_id': contract_id, 'reason': 'client marked as test'})
        contracts = len(ids)
        env_ids = c.execute(select(envelopes.c.id).where(envelopes.c.client_id == client_id, envelopes.c.status != 'template', envelopes.c.test.is_(False),
                                                         _envelope_scope(actor))).scalars().all()
        sent = len(_mark_envelope_rows(c, actor, env_ids, True))
    set_client(c, actor, client_id, test, 'marked by ' + actor['name'])
    return dict(id=client_id, test=test, agreements=contracts, envelopes=sent)


# ------------------------------------------------------------------ PDF watermark (signed copies and certificates of test envelopes)
def watermark_pdf(raw: bytes, text: str = TEST_WATERMARK) -> bytes:
    """The same light diagonal TEST mark as generated documents, over every page of a PDF."""
    from pypdf import PdfReader, PdfWriter, Transformation
    from reportlab.lib.colors import Color
    from reportlab.pdfgen import canvas

    reader = PdfReader(io.BytesIO(raw))
    writer = PdfWriter(clone_from=reader)
    stamps: dict[tuple[float, float], object] = {}
    for page in writer.pages:
        box = page.mediabox
        size = (round(float(box.width), 2), round(float(box.height), 2))
        if size not in stamps:
            buf = io.BytesIO()
            w, h = size
            pdf = canvas.Canvas(buf, pagesize=size)
            pdf.setFillColor(Color(0.914, 0.635, 0.231, alpha=0.32))
            diagonal = (w * w + h * h) ** 0.5
            font = 10.0
            while pdf.stringWidth(text, 'Helvetica-Bold', font + 1) < diagonal * 0.72 and font < 90:
                font += 1
            pdf.setFont('Helvetica-Bold', font)
            pdf.translate(w / 2, h / 2)
            pdf.rotate(45 if h >= w else 30)
            pdf.drawCentredString(0, -font / 3, text)
            pdf.save()
            stamps[size] = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
        page.merge_transformed_page(stamps[size], Transformation().translate(float(box.left), float(box.bottom)))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()

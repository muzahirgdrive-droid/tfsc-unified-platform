"""Follow-up notes on agreements: what was done to move an agreement forward, by whom and when.

A note belongs to a client and, when it is about one, to an envelope (out for signature) or a generated agreement (not sent
yet, or sent another way). Everyone who can see the client or the envelope sees its notes, newest first. Notes are a record:
they are never edited or deleted, and each one is also written to the Activity log.
"""
from __future__ import annotations

import time

from fastapi import Body, HTTPException, Request
from sqlalchemy import and_, func, or_, select

import database as db
import security as sec

CHANNELS = {'phone': 'Phone call', 'email': 'Email', 'meeting': 'Meeting', 'message': 'Text / chat', 'other': 'Other'}


def _client(c, actor, client_id):
    row = c.execute(select(db.clients.c.id, db.clients.c.name).where(db.clients.c.id == client_id, sec.scope(actor, db.clients.c.owner_id))).first()
    if not row:
        raise HTTPException(404, 'Client not found.')
    return row


def _envelope(c, actor, envelope_id):
    import esign
    return esign.get_envelope(c, actor, envelope_id)


def _contract(c, actor, contract_id):
    row = c.execute(select(db.contracts.c.id, db.contracts.c.client_id).where(db.contracts.c.id == contract_id, sec.scope(actor, db.contracts.c.owner_id),
                                                                             sec.company_scope(actor, db.contracts.c.entity))).first()
    if not row:
        raise HTTPException(404, 'Agreement not found.')
    return row


def view(r) -> dict:
    return {'id': r['id'], 'client_id': r['client_id'], 'envelope_id': r['envelope_id'], 'contract_id': r['contract_id'], 'channel': r['channel'],
            'channel_label': CHANNELS.get(r['channel'], 'Other'), 'body': r['body'], 'next_on': r['next_on'] or '', 'author': r['author_name'], 'created': r['created']}


def latest_for_envelopes(c, ids: list[str]) -> dict:
    """{envelope_id: (count, latest note)} for the rows of a list."""
    if not ids:
        return {}
    f = db.followups
    counts = dict(c.execute(select(f.c.envelope_id, func.count()).where(f.c.envelope_id.in_(ids)).group_by(f.c.envelope_id)).all())
    out = {}
    for r in c.execute(select(f).where(f.c.envelope_id.in_(list(counts))).order_by(f.c.created.desc())).mappings():
        if r['envelope_id'] not in out:
            out[r['envelope_id']] = {'count': counts[r['envelope_id']], 'latest': view(r)}
    return out


def install(app) -> None:
    @app.get('/api/followups')
    def api_list(request: Request, client_id: str = '', envelope_id: str = '', contract_id: str = ''):
        f = db.followups
        with db.engine.connect() as c:
            a = sec.get_actor(request, c)
            if envelope_id:
                e = _envelope(c, a, envelope_id)
                cond = or_(f.c.envelope_id == envelope_id, and_(f.c.contract_id == e['contract_id'], f.c.envelope_id.is_(None))) if e.get('contract_id') else f.c.envelope_id == envelope_id
            elif contract_id:
                _contract(c, a, contract_id)
                cond = f.c.contract_id == contract_id
            elif client_id:
                _client(c, a, client_id)
                cond = f.c.client_id == client_id
            else:
                raise HTTPException(400, 'Choose a client or an agreement.')
            rows = c.execute(select(f).where(cond).order_by(f.c.created.desc()).limit(500)).mappings().all()
            return {'notes': [view(r) for r in rows], 'channels': CHANNELS}

    @app.post('/api/followups')
    def api_add(request: Request, payload: dict = Body(...)):
        import server as legacy
        with db.engine.begin() as c:
            a = sec.get_actor(request, c)
            envelope_id, contract_id, client_id = payload.get('envelope_id') or None, payload.get('contract_id') or None, payload.get('client_id') or None
            if envelope_id:
                e = _envelope(c, a, envelope_id)
                client_id, contract_id = e.get('client_id'), e.get('contract_id') or contract_id
            elif contract_id:
                client_id = _contract(c, a, contract_id)[1]
            elif client_id:
                _client(c, a, client_id)
            else:
                raise HTTPException(400, 'Choose a client or an agreement.')
            channel = payload.get('channel') if payload.get('channel') in CHANNELS else 'other'
            body = legacy.clean(payload.get('body'), 'Note', True, 4000, typography=False)
            next_on = legacy.date_value(payload['next_on'], 'Next follow-up') if payload.get('next_on') else None
            row = dict(id=db.uid(), client_id=client_id, envelope_id=envelope_id, contract_id=contract_id, channel=channel, body=body, next_on=next_on,
                       author_id=a['id'], author_name=a['name'], created=time.time())
            c.execute(db.followups.insert().values(**row))
            db.log(c, a, 'followup.added', client_id, {'envelope': envelope_id, 'agreement': contract_id, 'channel': channel, 'next_on': next_on})
            return view(row)

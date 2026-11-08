"""Deleting clients: a 30-day Trash, restore, and the purge that follows.

Only a Super Admin deletes, restores or sees the Trash. Deleting moves clients to the Trash (deleted_at / deleted_by): they
leave every list, register, report, search and picker, but nothing about them changes, so a restore puts them back exactly as
they were, with the same client number. Envelopes already out for signature keep working: their links stay valid and the
signed copies are filed to the client as usual.

After 30 days a client is purged. Its details (address, profile, saved form, contacts) and the files the team uploaded to it
are removed for good. Signed agreements, Certificates of Completion, agreements signed before the portal and their signed
copies are legal records and are kept, tied to the hidden record, and stay findable in Agreements by envelope ID. The record
keeps only the name and number those documents carry; its number stays retired (client_number_registry) and its name is
free for a new client. The activity log keeps who deleted, restored and when it was purged.
"""
from __future__ import annotations

import time

from fastapi import Body, HTTPException, Request
from sqlalchemy import delete, func, select, update

import database as db
import security as sec
from esign_db import client_agreements, client_contacts, client_files, envelopes

RETENTION_DAYS = 30
RETENTION = RETENTION_DAYS * 86400
MAX_IDS = 500
OPEN = ('sent', 'completing')
KEEP_FILE_CATEGORIES = ('contract',)     # files filed as contracts may be signed agreements: kept like them


def live():
    """The condition every list, search and register uses: clients that are not in the Trash (or purged)."""
    return db.clients.c.deleted_at.is_(None)


def label(row) -> str:
    number = row.get('client_number') or ''
    return f"{row['name']} ({number})" if number else row['name']


def in_trash(row) -> HTTPException:
    if row.get('purged_at'):
        return HTTPException(404, 'Client not found.')
    return HTTPException(409, f"{label(row)} is in the Trash. A Super Admin can restore it from Clients › Trash; then you can work with it again.")


def refuse_if_trashed(row) -> None:
    """For a client found by id or name: work on a deleted client is refused with a message that says where it is."""
    if row and row.get('deleted_at'):
        raise in_trash(row)


def trashed_with_name(c, owner_id: str, name_key: str) -> dict | None:
    row = c.execute(select(db.clients.c.name, db.clients.c.client_number, db.clients.c.deleted_at, db.clients.c.purged_at)
                    .where(db.clients.c.owner_id == owner_id, db.clients.c.name_key == name_key, db.clients.c.deleted_at.isnot(None))).mappings().first()
    return dict(row) if row else None


def super_admin(actor: dict) -> None:
    if actor['role'] != 'super_admin':
        raise HTTPException(403, 'Only a Super Admin can delete or restore clients.')


def _ids(payload) -> list[str]:
    raw = payload.get('ids') if isinstance(payload, dict) else None
    if not isinstance(raw, list) or not raw or len(raw) > MAX_IDS or not all(isinstance(x, str) and 0 < len(x) <= 64 for x in raw):
        raise HTTPException(400, f'Choose between 1 and {MAX_IDS} clients.')
    return list(dict.fromkeys(raw))


def _companies(c, ids: list[str]) -> dict[str, list[str]]:
    """The companies each client works with: from its agreements (generated, sent, signed before the portal), else its saved form."""
    from entities import ENTITIES
    out: dict[str, list[str]] = {i: [] for i in ids}
    if not ids:
        return out
    pairs = set(c.execute(select(db.contracts.c.client_id, db.contracts.c.entity).where(db.contracts.c.client_id.in_(ids))).all())
    pairs |= set(c.execute(select(envelopes.c.client_id, envelopes.c.entity).where(envelopes.c.client_id.in_(ids), envelopes.c.status != 'template')).all())
    pairs |= set(c.execute(select(client_agreements.c.client_id, client_agreements.c.entity).where(client_agreements.c.client_id.in_(ids))).all())
    for cid, draft in c.execute(select(db.clients.c.id, db.clients.c.draft).where(db.clients.c.id.in_(ids))).all():
        if not any(p[0] == cid for p in pairs) and isinstance(draft, dict) and draft.get('entity'):
            pairs.add((cid, draft['entity']))
    for cid, entity in sorted(pairs, key=lambda x: (x[0], x[1] or '')):
        short = ENTITIES[entity]['short'] if entity in ENTITIES else entity
        if short and short not in out[cid]:
            out[cid].append(short)
    return out


def preview(c, actor: dict, ids: list[str]) -> dict:
    """What the confirmation shows: the clients, and the envelopes of theirs still out for signature."""
    super_admin(actor)
    rows = c.execute(select(db.clients.c.id, db.clients.c.name, db.clients.c.client_number).where(db.clients.c.id.in_(ids), live())).mappings().all()
    if len(rows) != len(ids):
        raise HTTPException(404, 'Some of these clients were not found or are already in the Trash. Reload the list.')
    companies = _companies(c, ids)
    waiting: dict[str, list[dict]] = {}
    for env_id, cid, ref, subject in c.execute(select(envelopes.c.id, envelopes.c.client_id, envelopes.c.ref, envelopes.c.subject)
                                               .where(envelopes.c.client_id.in_(ids), envelopes.c.status.in_(OPEN))).all():
        waiting.setdefault(cid, []).append({'id': env_id, 'ref': ref, 'subject': subject})
    order = {i: n for n, i in enumerate(ids)}
    clients = sorted(({'id': r['id'], 'name': r['name'], 'client_number': r['client_number'] or '', 'companies': companies.get(r['id'], []),
                       'waiting': waiting.get(r['id'], [])} for r in rows), key=lambda x: order[x['id']])
    return {'clients': clients, 'restorable_until': time.time() + RETENTION, 'days': RETENTION_DAYS}


def delete_clients(c, actor: dict, ids: list[str]) -> dict:
    super_admin(actor)
    t = time.time()
    rows = c.execute(select(db.clients.c.id, db.clients.c.name, db.clients.c.client_number).where(db.clients.c.id.in_(ids), live())).mappings().all()
    if len(rows) != len(ids):
        raise HTTPException(404, 'Some of these clients were not found or are already in the Trash. Reload the list.')
    for r in rows:
        c.execute(update(db.clients).where(db.clients.c.id == r['id']).values(deleted_at=t, deleted_by=actor['id']))
        waiting = c.execute(select(func.count()).select_from(envelopes).where(envelopes.c.client_id == r['id'], envelopes.c.status.in_(OPEN))).scalar()
        db.log(c, actor, 'client.deleted', r['id'], {'client_number': r['client_number'], 'name': r['name'], 'restorable_until': t + RETENTION,
                                                     'envelopes_out_for_signature': waiting, 'bulk': len(rows) > 1})
    return {'deleted': len(rows), 'restorable_until': t + RETENTION}


def restore(c, actor: dict, ids: list[str]) -> dict:
    super_admin(actor)
    rows = c.execute(select(db.clients.c.id, db.clients.c.name, db.clients.c.client_number).where(
        db.clients.c.id.in_(ids), db.clients.c.deleted_at.isnot(None), db.clients.c.purged_at.is_(None))).mappings().all()
    if len(rows) != len(ids):
        raise HTTPException(404, 'Some of these clients are no longer in the Trash. Reload the list.')
    for r in rows:
        c.execute(update(db.clients).where(db.clients.c.id == r['id']).values(deleted_at=None, deleted_by=None))
        db.log(c, actor, 'client.restored', r['id'], {'client_number': r['client_number'], 'name': r['name']})
    return {'restored': len(rows), 'clients': [{'id': r['id'], 'name': r['name'], 'client_number': r['client_number'] or ''} for r in rows]}


def trash(c, actor: dict) -> dict:
    super_admin(actor)
    purge_due(c)
    t = time.time()
    users = dict(c.execute(select(db.users.c.id, db.users.c.name)).all())
    rows = c.execute(select(db.clients.c.id, db.clients.c.name, db.clients.c.client_number, db.clients.c.address, db.clients.c.deleted_at,
                            db.clients.c.deleted_by).where(db.clients.c.deleted_at.isnot(None), db.clients.c.purged_at.is_(None))
                     .order_by(db.clients.c.deleted_at.desc())).mappings().all()
    companies = _companies(c, [r['id'] for r in rows])
    out = []
    for r in rows:
        purge_at = r['deleted_at'] + RETENTION
        out.append({'id': r['id'], 'name': r['name'], 'client_number': r['client_number'] or '', 'address': r['address'] or '',
                    'companies': companies.get(r['id'], []), 'deleted_at': r['deleted_at'], 'deleted_by': users.get(r['deleted_by'], ''),
                    'purge_at': purge_at, 'days_left': max(0, int((purge_at - t) // 86400) + (1 if (purge_at - t) % 86400 else 0))})
    month = time.strftime('%Y-%m', time.gmtime(t))
    restored = c.execute(select(func.count()).select_from(db.audit).where(db.audit.c.action == 'client.restored',
                                                                         db.audit.c.created >= time.mktime(time.strptime(month + '-01', '%Y-%m-%d')))).scalar()
    # retired: numbers whose client is gone for good (purged, or removed before numbers were kept); clients in the Trash keep theirs
    retired = c.execute(select(func.count()).select_from(db.client_number_registry).where(
        db.client_number_registry.c.client_id.notin_(select(db.clients.c.id).where(db.clients.c.purged_at.is_(None))))).scalar()
    return {'clients': out, 'days': RETENTION_DAYS, 'restored_this_month': restored, 'retired_numbers': retired,
            'next_purge': out[-1] if out else None}


def count(c, actor: dict) -> int:
    if actor['role'] != 'super_admin':
        return 0
    return c.execute(select(func.count()).select_from(db.clients).where(db.clients.c.deleted_at.isnot(None), db.clients.c.purged_at.is_(None))).scalar()


def purge_due(c, now: float | None = None) -> int:
    """Purge every client that has been in the Trash for 30 days. Returns how many."""
    t = now or time.time()
    due = c.execute(select(db.clients.c.id, db.clients.c.name, db.clients.c.client_number, db.clients.c.draft).where(
        db.clients.c.deleted_at.isnot(None), db.clients.c.purged_at.is_(None), db.clients.c.deleted_at <= t - RETENTION)).mappings().all()
    for r in due:
        draft = r['draft'] or {}
        kept_files = select(client_agreements.c.file_id).where(client_agreements.c.client_id == r['id'], client_agreements.c.file_id.isnot(None))
        removed = c.execute(delete(client_files).where(client_files.c.client_id == r['id'], client_files.c.category.notin_(KEEP_FILE_CATEGORIES),
                                                       client_files.c.id.notin_(kept_files))).rowcount
        contacts = c.execute(delete(client_contacts).where(client_contacts.c.client_id == r['id'])).rowcount
        c.execute(update(db.clients).where(db.clients.c.id == r['id']).values(
            purged_at=t, name_key=('purged-' + r['id'])[:64], address='', profile=None,
            draft={'entity': draft['entity']} if isinstance(draft, dict) and draft.get('entity') else {}))
        db.log(c, None, 'client.purged', r['id'], {'client_number': r['client_number'], 'name': r['name'], 'files_removed': removed,
                                                  'contacts_removed': contacts, 'kept': 'signed agreements and certificates'})
    return len(due)


def purge_once() -> int:
    """Background pass (e-signature worker, once an hour)."""
    import esign
    with db.engine.connect() as c:
        if not c.execute(select(db.clients.c.id).where(db.clients.c.deleted_at.isnot(None), db.clients.c.purged_at.is_(None),
                                                       db.clients.c.deleted_at <= time.time() - RETENTION).limit(1)).first():
            return 0
    with esign.write_tx() as c:
        return purge_due(c)


def install(app) -> None:
    import esign

    @app.get('/api/client-records/delete-preview')
    def api_preview(request: Request, ids: str = ''):
        with db.engine.connect() as c:
            a = sec.get_actor(request, c)
            return preview(c, a, _ids({'ids': [x for x in ids.split(',') if x]}))

    @app.post('/api/client-records/delete')
    def api_delete(request: Request, payload: dict = Body(...)):
        with esign.write_tx() as c:
            a = sec.get_actor(request, c)
            return delete_clients(c, a, _ids(payload))

    @app.post('/api/client-records/restore')
    def api_restore(request: Request, payload: dict = Body(...)):
        with esign.write_tx() as c:
            a = sec.get_actor(request, c)
            return restore(c, a, _ids(payload))

    @app.get('/api/client-records/trash')
    def api_trash(request: Request):
        with esign.write_tx() as c:          # a pass of the purge runs first
            a = sec.get_actor(request, c)
            return trash(c, a)


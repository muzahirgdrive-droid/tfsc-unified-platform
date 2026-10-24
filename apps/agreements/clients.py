"""Client profiles: the client's details, the people who work there, and every document kept for them.

The document library brings together the agreements generated for the client, everything sent for signature (the
original documents while out for signature; the signed documents and the Certificate of Completion once completed),
files the client attached to email replies or while signing, and files uploaded to the profile. Recipients of sent
envelopes are added to the client's contacts, and the contacts feed the recipient address book.

Uploaded files and everything the client sends are data: they are stored, scanned for viruses and downloaded, never
opened or interpreted by the server.
"""
from __future__ import annotations

import base64
import hashlib
import re
import time

from fastapi import Body, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

import database as db
import esign
import mailer
import security as sec
from esign_db import client_contacts, client_files, envelopes, inbound, inbound_files, recipients
from esign_db import documents as esign_documents

CONTACT_ROLES = ('signatory', 'billing', 'compliance', 'other')
FILE_CATEGORIES = ('contract', 'correspondence', 'identification', 'insurance', 'other')
FILE_LIMIT = 15_000_000
FILE_TYPES = {'.pdf': 'application/pdf', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              '.doc': 'application/msword', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
              '.xls': 'application/vnd.ms-excel', '.csv': 'text/csv', '.txt': 'text/plain', '.png': 'image/png', '.jpg': 'image/jpeg',
              '.jpeg': 'image/jpeg', '.odt': 'application/vnd.oasis.opendocument.text', '.rtf': 'application/rtf'}
MAGIC = {'.pdf': (b'%PDF',), '.png': (b'\x89PNG\r\n\x1a\n',), '.jpg': (b'\xff\xd8\xff',), '.jpeg': (b'\xff\xd8\xff',), '.docx': (b'PK\x03\x04',),
         '.xlsx': (b'PK\x03\x04',), '.odt': (b'PK\x03\x04',), '.doc': (b'\xd0\xcf\x11\xe0',), '.xls': (b'\xd0\xcf\x11\xe0',), '.rtf': (b'{\\rtf',)}


def clean(value, label, limit, required=False):
    return esign.clean(value, label, limit, required)


def name_key(name: str) -> str:
    return sec.digest(name.strip().casefold())


def client_for(c, actor: dict, client_id: str) -> dict:
    row = c.execute(select(db.clients).where(db.clients.c.id == client_id, sec.scope(actor, db.clients.c.owner_id))).mappings().first()
    if not row:
        raise HTTPException(404, 'Client not found.')
    return dict(row)


def profile_values(payload: dict, current: dict | None = None) -> dict:
    out = dict(current or {})
    for key, label, limit in (('phone', 'Phone', 40), ('email', 'Email', 200), ('website', 'Website', 200), ('notes', 'Notes', 4000),
                              ('industry', 'Specialty', 120), ('npi', 'NPI', 20), ('tax_id', 'Tax ID', 20)):
        if key in payload:
            out[key] = clean(payload[key], label, limit)
    if out.get('email'):
        out['email'] = mailer.valid_email(out['email'])
    if out.get('npi') and not re.fullmatch(r'\d{10}', out['npi']):
        raise ValueError('An NPI has 10 digits.')
    return out


def ensure_client(c, actor: dict, name: str, entity: str, email: str = '', signatory: str = '') -> str:
    """The id of the actor's client with this name, created when missing (used by bulk send)."""
    name = clean(name, 'Client name', 200, True)
    row = c.execute(select(db.clients.c.id).where(db.clients.c.owner_id == actor['id'], db.clients.c.name_key == name_key(name))).first()
    if row:
        return row[0]
    t = time.time()
    cid = db.uid()
    draft = {'client_name': name, 'address': '', 'entity': entity, 'client_email': email, 'client_signatory': signatory, 'id': cid}
    c.execute(db.clients.insert().values(id=cid, owner_id=actor['id'], name=name, name_key=name_key(name), address='', draft=draft, updated=t,
                                         profile={}, created=t))
    db.log(c, actor, 'client.created', cid, {'entity': entity, 'source': 'bulk send'})
    return cid


def contact_row(c, client_id: str, payload: dict, actor: dict, existing: dict | None = None) -> dict:
    role = payload.get('role', existing['role'] if existing else 'other')
    if role not in CONTACT_ROLES:
        raise ValueError('Choose the contact\'s role.')
    email = clean(payload.get('email', existing['email'] if existing else ''), 'Email', 200)
    row = dict(name=clean(payload.get('name', existing['name'] if existing else ''), 'Name', 150, True),
               email=mailer.valid_email(email) if email else '', title=clean(payload.get('title', existing['title'] if existing else ''), 'Title', 150),
               phone=clean(payload.get('phone', existing['phone'] if existing else ''), 'Phone', 40), role=role,
               notes=clean(payload.get('notes', existing['notes'] if existing else ''), 'Notes', 2000), updated=time.time())
    if row['email']:
        duplicate = c.execute(select(client_contacts.c.id).where(client_contacts.c.client_id == client_id, func.lower(client_contacts.c.email) == row['email'].lower(),
                                                                 client_contacts.c.id != (existing['id'] if existing else ''))).first()
        if duplicate:
            raise HTTPException(409, 'This client already has a contact with that email address.')
    return row


def remember_contacts(c, envelope: dict) -> None:
    """Recipients of an envelope sent for a client become that client's contacts (once per email address)."""
    if not envelope.get('client_id'):
        return
    known = {e.lower() for (e,) in c.execute(select(client_contacts.c.email).where(client_contacts.c.client_id == envelope['client_id']))}
    t = time.time()
    for r in c.execute(select(recipients).where(recipients.c.envelope_id == envelope['id'])).mappings():
        if not r['email'] or r['email'].lower() in known or r['role_name'] == 'Company signer':
            continue
        if any(r['email'].lower().endswith('@' + d) for d in company_domains()):
            continue
        known.add(r['email'].lower())
        c.execute(client_contacts.insert().values(id=db.uid(), client_id=envelope['client_id'], name=r['name'], email=r['email'], title='', phone='',
                                                  role='signatory' if r['role'] in esign.SIGNING_ROLES else 'other', notes='', created=t, updated=t,
                                                  created_by=envelope['owner_id']))


def company_domains() -> set[str]:
    """Our own companies' email domains: their people are not client contacts."""
    return {a.rsplit('@', 1)[-1].lower() for a in mailer.DEFAULT_ADDRESSES.values()}


def seed_contacts(c, client: dict) -> None:
    """The signatory entered when the agreement was prepared is the client's first contact."""
    draft = client['draft'] or {}
    if c.execute(select(client_contacts.c.id).where(client_contacts.c.client_id == client['id']).limit(1)).first():
        return
    name, email = (draft.get('client_signatory') or '').strip(), (draft.get('client_email') or '').strip()
    if not name:
        return
    try:
        email = mailer.valid_email(email) if email else ''
    except ValueError:
        email = ''
    t = time.time()
    c.execute(client_contacts.insert().values(id=db.uid(), client_id=client['id'], name=name[:150], email=email, title=(draft.get('client_title') or '')[:150],
                                              phone='', role='signatory', notes='', created=t, updated=t, created_by=client['owner_id']))


def contact_view(row) -> dict:
    return {k: row[k] for k in ('id', 'name', 'email', 'title', 'phone', 'role', 'notes', 'created', 'updated')}


def library(c, actor: dict, client: dict) -> list[dict]:
    """Every document kept for a client, newest first."""
    items = []
    contracts = c.execute(select(db.contracts.c.id, db.contracts.c.entity, db.contracts.c.created).where(
        db.contracts.c.client_id == client['id'], sec.scope(actor, db.contracts.c.owner_id), sec.company_scope(actor, db.contracts.c.entity))).mappings().all()
    created = {r['id']: r for r in contracts}
    if contracts:
        for d in c.execute(select(db.documents.c.id, db.documents.c.name, db.documents.c.kind, db.documents.c.agreement, db.documents.c.size,
                                  db.documents.c.contract_id, db.documents.c.sha256).where(db.documents.c.contract_id.in_(list(created)))).mappings():
            contract = created[d['contract_id']]
            items.append({'id': d['id'], 'name': d['name'], 'group': 'Generated agreements', 'kind': d['kind'].upper(), 'size': d['size'],
                          'created': contract['created'], 'company': contract['entity'], 'url': f"/files/{d['id']}", 'sha256': d['sha256']})
    sent = c.execute(select(envelopes.c.id, envelopes.c.ref, envelopes.c.subject, envelopes.c.status, envelopes.c.entity, envelopes.c.created,
                            envelopes.c.completed_at, envelopes.c.sent_at, envelopes.c.certificate_sha256).where(
        envelopes.c.client_id == client['id'], envelopes.c.status.notin_(('template', 'draft')), esign.visible(actor))).mappings().all()
    by_id = {e['id']: e for e in sent}
    if sent:
        for d in c.execute(select(esign_documents.c.id, esign_documents.c.name, esign_documents.c.envelope_id, esign_documents.c.source,
                                  esign_documents.c.final_sha256, esign_documents.c.sha256, esign_documents.c.pages)
                           .where(esign_documents.c.envelope_id.in_(list(by_id))).order_by(esign_documents.c.position)).mappings():
            e = by_id[d['envelope_id']]
            done = e['status'] == 'completed' and d['final_sha256']
            group = 'Signed documents' if done else ('Attachments from signers' if d['source'] == 'attachment' else 'Sent for signature')
            if done and d['source'] == 'attachment':
                group = 'Attachments from signers'
            items.append({'id': d['id'], 'name': d['name'], 'group': group, 'kind': 'PDF', 'created': e['completed_at'] if done else e['sent_at'] or e['created'],
                          'company': e['entity'], 'envelope': e['ref'], 'envelope_id': e['id'], 'status': e['status'], 'pages': d['pages'],
                          'url': f"/api/esign/documents/{d['id']}/file" + ('?final=1' if done else ''), 'sha256': d['final_sha256'] if done else d['sha256']})
        for e in sent:
            if e['status'] == 'completed' and e['certificate_sha256']:
                items.append({'id': e['id'] + '-certificate', 'name': f"Certificate of Completion {e['ref']}.pdf", 'group': 'Certificates of Completion', 'kind': 'PDF',
                              'created': e['completed_at'], 'company': e['entity'], 'envelope': e['ref'], 'envelope_id': e['id'], 'status': e['status'],
                              'url': f"/api/esign/envelopes/{e['id']}/certificate", 'sha256': e['certificate_sha256']})
        for f in c.execute(select(inbound_files.c.id, inbound_files.c.name, inbound_files.c.size, inbound_files.c.scan, inbound.c.envelope_id, inbound.c.received,
                                  inbound.c.from_addr).join(inbound, inbound.c.id == inbound_files.c.inbound_id)
                           .where(inbound.c.envelope_id.in_(list(by_id)))).mappings():
            e = by_id[f['envelope_id']]
            items.append({'id': f['id'], 'name': f['name'], 'group': 'Email attachments from the client', 'kind': f['name'].rsplit('.', 1)[-1].upper(),
                          'size': f['size'], 'created': f['received'], 'company': e['entity'], 'envelope': e['ref'], 'envelope_id': e['id'],
                          'from': f['from_addr'], 'scan': f['scan'], 'url': f"/api/esign/attachments/{f['id']}"})
    users = {}
    for f in c.execute(select(client_files.c.id, client_files.c.name, client_files.c.size, client_files.c.category, client_files.c.note, client_files.c.created,
                              client_files.c.scan, client_files.c.uploaded_by, client_files.c.sha256).where(client_files.c.client_id == client['id'])).mappings():
        if f['uploaded_by'] and f['uploaded_by'] not in users:
            users[f['uploaded_by']] = c.execute(select(db.users.c.name).where(db.users.c.id == f['uploaded_by'])).scalar() or ''
        items.append({'id': f['id'], 'name': f['name'], 'group': 'Uploaded files', 'kind': f['name'].rsplit('.', 1)[-1].upper(), 'size': f['size'],
                      'created': f['created'], 'category': f['category'], 'note': f['note'], 'scan': f['scan'], 'uploaded_by': users.get(f['uploaded_by'], ''),
                      'url': f"/api/clients/files/{f['id']}", 'deletable': f['uploaded_by'] == actor['id'] or actor['role'] != 'user', 'sha256': f['sha256'],
                      'can_send': f['name'].lower().rsplit('.', 1)[-1] in ('pdf', 'docx', 'doc', 'odt', 'rtf', 'txt', 'png', 'jpg', 'jpeg')})
    items.sort(key=lambda x: x.get('created') or 0, reverse=True)
    return items


def file_for(c, actor: dict, file_id: str) -> dict:
    row = c.execute(select(client_files).where(client_files.c.id == file_id)).mappings().first()
    if not row:
        raise HTTPException(404, 'File not found.')
    client_for(c, actor, row['client_id'])
    return dict(row)


def checked_upload(payload: dict) -> dict:
    """Validate and virus-scan an uploaded client file (before any database lock)."""
    if not isinstance(payload, dict):
        raise ValueError('Invalid request.')
    try:
        raw = base64.b64decode(str(payload.get('data', '')), validate=True)
    except (ValueError, TypeError):
        raise ValueError('The file could not be read.') from None
    if not 1 <= len(raw) <= FILE_LIMIT:
        raise ValueError('Upload a file smaller than 15 MB.')
    name = re.sub(r'[^\w .()&,-]', '_', clean(payload.get('name'), 'File name', 200, True))
    suffix = ('.' + name.rsplit('.', 1)[-1].lower()) if '.' in name else ''
    if suffix not in FILE_TYPES:
        raise ValueError('Upload a PDF, Word, Excel, CSV, text, PNG or JPEG file.')
    if suffix in MAGIC and not raw.startswith(MAGIC[suffix]):
        raise ValueError('The file does not match its type. Check the file and upload it again.')
    if suffix in ('.csv', '.txt'):
        try:
            raw.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError('Text and CSV files must be UTF-8 text.') from None
    category = payload.get('category') or 'other'
    if category not in FILE_CATEGORIES:
        raise ValueError('Choose a category for the file.')
    verdict = esign.scan_upload(raw)
    return dict(name=name, content_type=FILE_TYPES[suffix], body=raw, size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), scan=verdict,
                category=category, note=clean(payload.get('note'), 'Note', 1000))


def install(app) -> None:  # noqa: C901  (route table)
    def actor_of(request, c):
        return sec.get_actor(request, c)

    @app.post('/api/clients/new')
    def api_new_client(request: Request, payload: dict = Body(...)):
        with esign.write_tx() as c:
            a = actor_of(request, c)
            name = clean(payload.get('name'), 'Client name', 200, True)
            entity = payload.get('entity') or (a['companies'][0] if a['companies'] else '')
            sec.ensure_company(a, entity)
            existing = c.execute(select(db.clients.c.id).where(db.clients.c.owner_id == a['id'], db.clients.c.name_key == name_key(name))).first()
            if existing:
                raise HTTPException(409, 'You already have a client with this name. Open their profile.')
            t = time.time()
            cid = db.uid()
            address = clean(payload.get('address'), 'Address', 500)
            profile = profile_values(payload)
            draft = {'client_name': name, 'address': address, 'entity': entity, 'client_email': profile.get('email', ''), 'id': cid}
            c.execute(db.clients.insert().values(id=cid, owner_id=a['id'], name=name, name_key=name_key(name), address=address, draft=draft, updated=t,
                                                 profile=profile, created=t))
            db.log(c, a, 'client.created', cid, {'entity': entity})
            return {'id': cid}

    @app.get('/api/clients/{client_id}/profile')
    def api_profile(client_id: str, request: Request):
        with esign.write_tx() as c:
            a = actor_of(request, c)
            client = client_for(c, a, client_id)
            seed_contacts(c, client)
            db.log(c, a, 'client.viewed', client_id, {'client': client['name']})
            owner = c.execute(select(db.users.c.name).where(db.users.c.id == client['owner_id'])).scalar()
            contacts = [contact_view(r) for r in c.execute(select(client_contacts).where(client_contacts.c.client_id == client_id)
                                                          .order_by(client_contacts.c.role, client_contacts.c.name)).mappings()]
            statuses = dict(c.execute(select(envelopes.c.status, func.count()).where(envelopes.c.client_id == client_id, esign.visible(a),
                                                                                     envelopes.c.status != 'template').group_by(envelopes.c.status)).all())
            contracts = c.execute(select(func.count()).select_from(db.contracts).where(db.contracts.c.client_id == client_id, sec.scope(a, db.contracts.c.owner_id),
                                                                                       sec.company_scope(a, db.contracts.c.entity))).scalar()
            files = c.execute(select(func.count()).select_from(client_files).where(client_files.c.client_id == client_id)).scalar()
            draft = client['draft'] or {}
            return {'client': {'id': client['id'], 'name': client['name'], 'address': client['address'], 'owner': owner, 'created': client['created'],
                               'updated': client['updated'], 'profile': client['profile'] or {}, 'entity': draft.get('entity', ''),
                               'signatory': draft.get('client_signatory', ''), 'signatory_title': draft.get('client_title', ''),
                               'signatory_email': draft.get('client_email', '')},
                    'contacts': contacts, 'counts': {'contracts': contracts, 'envelopes': statuses, 'files': files},
                    'can_edit': client['owner_id'] == a['id'] or a['role'] != 'user'}

    @app.post('/api/clients/{client_id}/profile')
    def api_profile_save(client_id: str, request: Request, payload: dict = Body(...)):
        with esign.write_tx() as c:
            a = actor_of(request, c)
            client = client_for(c, a, client_id)
            values = {'profile': profile_values(payload, client['profile']), 'updated': time.time()}
            draft = dict(client['draft'] or {})
            if 'name' in payload:
                values['name'] = clean(payload['name'], 'Client name', 200, True)
                values['name_key'] = name_key(values['name'])
                draft['client_name'] = values['name']
            if 'address' in payload:
                values['address'] = clean(payload['address'], 'Address', 500)
                draft['address'] = values['address']
            values['draft'] = draft
            try:
                c.execute(update(db.clients).where(db.clients.c.id == client_id).values(**values))
            except IntegrityError:
                raise HTTPException(409, 'Another client of this owner already has that name.') from None
            db.log(c, a, 'client.updated', client_id, {'fields': sorted(k for k in payload if k in ('name', 'address', 'phone', 'email', 'website', 'notes',
                                                                                                 'industry', 'npi', 'tax_id'))})
            return {'ok': True}

    @app.post('/api/clients/{client_id}/contacts')
    def api_contact_add(client_id: str, request: Request, payload: dict = Body(...)):
        with esign.write_tx() as c:
            a = actor_of(request, c)
            client_for(c, a, client_id)
            row = contact_row(c, client_id, payload, a)
            row.update(id=db.uid(), client_id=client_id, created=row['updated'], created_by=a['id'])
            c.execute(client_contacts.insert().values(**row))
            db.log(c, a, 'client.contact_added', client_id, {'contact': row['name']})
            return contact_view(row)

    @app.post('/api/clients/{client_id}/contacts/{contact_id}')
    def api_contact_save(client_id: str, contact_id: str, request: Request, payload: dict = Body(...)):
        with esign.write_tx() as c:
            a = actor_of(request, c)
            client_for(c, a, client_id)
            existing = c.execute(select(client_contacts).where(client_contacts.c.id == contact_id, client_contacts.c.client_id == client_id)).mappings().first()
            if not existing:
                raise HTTPException(404, 'Contact not found.')
            if payload.get('delete') is True:
                c.execute(delete(client_contacts).where(client_contacts.c.id == contact_id))
                db.log(c, a, 'client.contact_removed', client_id, {'contact': existing['name']})
                return {'ok': True}
            row = contact_row(c, client_id, payload, a, dict(existing))
            c.execute(update(client_contacts).where(client_contacts.c.id == contact_id).values(**row))
            return contact_view(dict(existing) | row)

    @app.get('/api/clients/{client_id}/documents')
    def api_library(client_id: str, request: Request):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            return {'documents': library(c, a, client_for(c, a, client_id))}

    @app.get('/api/clients/{client_id}/envelopes')
    def api_client_envelopes(client_id: str, request: Request, page: int = 0):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            client_for(c, a, client_id)
            return esign.list_envelopes(c, a, 'all', '', page, client_id=client_id)

    @app.post('/api/clients/{client_id}/files')
    def api_file_upload(client_id: str, request: Request, payload: dict = Body(...)):
        with db.engine.connect() as c:          # access is checked before the file is scanned
            client_for(c, actor_of(request, c), client_id)
        upload = checked_upload(payload)
        with esign.write_tx() as c:
            a = actor_of(request, c)
            client_for(c, a, client_id)
            if c.execute(select(func.count()).select_from(client_files).where(client_files.c.client_id == client_id)).scalar() >= 2000:
                raise HTTPException(400, 'This client has too many files. Remove some first.')
            row = dict(upload, id=db.uid(), client_id=client_id, uploaded_by=a['id'], created=time.time())
            c.execute(client_files.insert().values(**row))
            db.log(c, a, 'client.file_uploaded', client_id, {'document': row['name'], 'sha256': row['sha256'], 'scan': row['scan'], 'category': row['category']})
            return {'id': row['id'], 'name': row['name'], 'scan': row['scan']}

    @app.get('/api/clients/files/{file_id}')
    def api_file_download(file_id: str, request: Request):
        with db.engine.begin() as c:
            a = actor_of(request, c)
            row = file_for(c, a, file_id)
            db.log(c, a, 'client.file_downloaded', row['client_id'], {'document': row['name'], 'sha256': row['sha256']})
        media = 'application/pdf' if row['content_type'] == 'application/pdf' else 'application/octet-stream'
        return Response(bytes(row['body']), media_type=media, headers={'Content-Disposition': 'attachment; filename="' + row['name'].replace('"', '') + '"'})

    @app.post('/api/clients/files/{file_id}/delete')
    def api_file_delete(file_id: str, request: Request):
        with esign.write_tx() as c:
            a = actor_of(request, c)
            row = file_for(c, a, file_id)
            if row['uploaded_by'] != a['id'] and a['role'] == 'user':
                raise HTTPException(403, 'Only the person who uploaded this file or an administrator can remove it.')
            c.execute(delete(client_files).where(client_files.c.id == file_id))
            db.log(c, a, 'client.file_removed', row['client_id'], {'document': row['name'], 'sha256': row['sha256']})
            return {'ok': True}

    @app.post('/api/clients/files/{file_id}/send')
    def api_file_send(file_id: str, request: Request, payload: dict = Body(default={})):
        """Start an envelope for the client with this file as its document."""
        with db.engine.connect() as c:
            a = actor_of(request, c)
            row = file_for(c, a, file_id)
        import esign_pdf
        pdf, name = esign_pdf.to_pdf(row['name'], bytes(row['body']))
        with esign.write_tx() as c:
            a = actor_of(request, c)
            row = file_for(c, a, file_id)
            client = client_for(c, a, row['client_id'])
            entity = payload.get('entity') or (client['draft'] or {}).get('entity') or (a['companies'][0] if a['companies'] else '')
            envelope = esign.create(c, a, {'entity': entity, 'client_id': client['id'], 'subject': f"Please sign: {name[:-4]} - {client['name']}"[:200]})
            esign.add_document(c, envelope, name, pdf, 'upload')
            return esign.detail(c, a, esign.get_envelope(c, a, envelope['id']))

    @app.get('/api/contacts')
    def api_contacts(request: Request, q: str = ''):
        """Address book for recipients: client contacts and people who received envelopes, matching the search."""
        term = q.strip().lower()[:100]
        if len(term) < 2:
            return {'contacts': []}
        with db.engine.connect() as c:
            a = actor_of(request, c)
            visible_clients = select(db.clients.c.id).where(sec.scope(a, db.clients.c.owner_id))
            match = or_(func.lower(client_contacts.c.name).contains(term, autoescape=True), func.lower(client_contacts.c.email).contains(term, autoescape=True))
            out, seen = [], set()
            for r in c.execute(select(client_contacts.c.name, client_contacts.c.email, client_contacts.c.title, db.clients.c.name.label('client'))
                               .join(db.clients, db.clients.c.id == client_contacts.c.client_id)
                               .where(client_contacts.c.client_id.in_(visible_clients), client_contacts.c.email != '', match)
                               .order_by(client_contacts.c.name).limit(20)).mappings():
                if r['email'].lower() not in seen:
                    seen.add(r['email'].lower())
                    out.append(dict(r))
            rmatch = or_(func.lower(recipients.c.name).contains(term, autoescape=True), func.lower(recipients.c.email).contains(term, autoescape=True))
            for r in c.execute(select(recipients.c.name, recipients.c.email).join(envelopes, envelopes.c.id == recipients.c.envelope_id)
                               .where(esign.visible(a), recipients.c.email != '', rmatch).order_by(recipients.c.created.desc()).limit(60)).mappings():
                if r['email'].lower() not in seen and len(out) < 20:
                    seen.add(r['email'].lower())
                    out.append({'name': r['name'], 'email': r['email'], 'title': '', 'client': ''})
            users = c.execute(select(db.users.c.name, db.users.c.email).where(db.users.c.active == True, or_(  # noqa: E712
                func.lower(db.users.c.name).contains(term, autoescape=True), func.lower(db.users.c.email).contains(term, autoescape=True))).limit(10)).mappings()
            for u in users:
                if u['email'].lower() not in seen and len(out) < 25:
                    seen.add(u['email'].lower())
                    out.append({'name': u['name'], 'email': u['email'], 'title': '', 'client': 'Your team'})
            return {'contacts': out}


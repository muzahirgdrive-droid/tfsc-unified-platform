"""Electronic signature: envelopes, recipients, fields, signing, routing, completion and the audit trail.

Lifecycle of an envelope: draft -> sent -> completing -> completed, or sent -> declined / voided / expired.
Recipients are notified in routing order; recipients with the same order act in parallel. Signers place their
signature, initials and other fields; "needs to view" recipients acknowledge; "receives a copy" recipients get copies.
When every signer and viewer is done, the worker stamps the fields onto the documents, writes the Certificate of
Completion and emails everyone their copy.

Integrity: documents are frozen (SHA-256) when the envelope is sent; every event is appended to a per-envelope
SHA-256 hash chain; completed documents and the certificate are hashed and can be checked at /verify.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import logging
import re
import secrets
import time
import zoneinfo

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import and_, delete, func, or_, select, text, update

import database as db
import esign_pdf
import mailer
import security as sec
from entities import ENTITIES, get_entity
from esign_db import documents, envelopes, events, fields, inbound, inbound_files, outbox, recipients, sign_sessions, tokens

log = logging.getLogger('tfsc.esign')
ROLES = ('signer', 'viewer', 'cc')
ROLE_LABELS = {'signer': 'Needs to sign', 'viewer': 'Needs to view', 'cc': 'Receives a copy'}
KINDS = {'signature': (170, 40), 'initials': (64, 24), 'date_signed': (110, 16), 'name': (150, 16), 'email': (170, 16), 'company': (150, 16),
         'title': (150, 16), 'text': (150, 16), 'checkbox': (14, 14), 'dropdown': (150, 16)}
INPUT_KINDS = ('company', 'title', 'text', 'checkbox', 'dropdown')
SETTINGS = {'reminder_delay': (2, 1, 30), 'reminder_every': (3, 0, 30), 'expire_days': (30, 1, 365), 'warn_days': (3, 0, 30)}
OPEN_STATUSES = ('sent', 'delivered')
DONE_STATUSES = ('signed', 'completed')
PAGE_DPI = 120
LINK_DAYS_AFTER_COMPLETION = 60
SESSION_SECONDS = 4 * 3600
LABELS = {
    'created': 'Envelope created', 'sent': 'Envelope sent', 'routed': 'Sent to recipient', 'delivered': 'Viewed by recipient',
    'consent': 'Accepted electronic record and signature disclosure', 'auth_passed': 'Access code verified', 'auth_failed': 'Wrong access code entered',
    'locked': 'Access locked after repeated wrong access codes', 'adopted': 'Signature adopted', 'signed': 'Signed', 'reviewed': 'Review completed',
    'declined': 'Declined to sign', 'voided': 'Envelope voided', 'corrected': 'Envelope corrected', 'resent': 'Envelope resent',
    'reminder': 'Reminder sent', 'expiring': 'Expiration warning sent', 'expired': 'Envelope expired', 'completed': 'Envelope completed',
    'bounced': 'Email undeliverable', 'mail_failed': 'Email could not be sent', 'replied': 'Reply received by email', 'portal_session': 'Opened from a TFSC Agreements account',
}


def now() -> float:
    return time.time()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def clean(value, label: str, limit: int, required=False) -> str:
    if value is None:
        value = ''
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError(f'{label} must be text.')
    value = ''.join(ch for ch in str(value).strip() if ch in '\n\t' or ord(ch) >= 32)
    if required and not value:
        raise ValueError(f'{label} is required.')
    if len(value) > limit:
        raise ValueError(f'{label} must be {limit} characters or fewer.')
    return value


def write_tx():
    """A write transaction that takes SQLite's write lock up front (PostgreSQL uses row locks instead)."""
    class _Tx:
        def __enter__(self):
            self.cm = db.engine.begin()
            self.c = self.cm.__enter__()
            if self.c.dialect.name == 'sqlite':
                self.c.execute(text('BEGIN IMMEDIATE'))
            return self.c

        def __exit__(self, *exc):
            return self.cm.__exit__(*exc)
    return _Tx()


def lock(c, envelope_id: str) -> dict:
    q = select(envelopes).where(envelopes.c.id == envelope_id)
    if c.dialect.name == 'postgresql':
        q = q.with_for_update()
    row = c.execute(q).mappings().first()
    if not row:
        raise HTTPException(404, 'Envelope not found.')
    return dict(row)


# ------------------------------------------------------------------ audit trail (hash chain)
def canonical(row: dict) -> str:
    keep = {k: row[k] for k in ('id', 'envelope_id', 'seq', 'recipient_id', 'at', 'kind', 'actor', 'ip', 'user_agent', 'detail')}
    return json.dumps(keep, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)


def build_event(c, envelope_id: str, kind: str, actor: str, *, recipient_id=None, ip='', ua='', detail=None, at=None) -> dict:
    last = c.execute(select(events.c.seq, events.c.hash).where(events.c.envelope_id == envelope_id).order_by(events.c.seq.desc()).limit(1)).first()
    row = dict(id=db.uid(), envelope_id=envelope_id, seq=(last[0] + 1) if last else 1, recipient_id=recipient_id, at=at or now(), kind=kind,
               actor=(actor or '')[:250], ip=(ip or '')[:64], user_agent=(ua or '')[:300], detail=detail or {}, prev_hash=last[1] if last else '0' * 64)
    row['hash'] = hashlib.sha256((row['prev_hash'] + canonical(row)).encode()).hexdigest()
    return row


def record(c, envelope_id: str, kind: str, actor: str, **kw) -> dict:
    row = build_event(c, envelope_id, kind, actor, **kw)
    c.execute(events.insert().values(**row))
    return row


def verify_chain(rows: list[dict]) -> bool:
    previous = '0' * 64
    for i, row in enumerate(sorted(rows, key=lambda r: r['seq']), start=1):
        if row['seq'] != i or row['prev_hash'] != previous:
            return False
        if hashlib.sha256((previous + canonical(row)).encode()).hexdigest() != row['hash']:
            return False
        previous = row['hash']
    return True


# ------------------------------------------------------------------ access
def visible(actor: dict):
    return and_(sec.scope(actor, envelopes.c.owner_id), sec.company_scope(actor, envelopes.c.entity))


def get_envelope(c, actor: dict, envelope_id: str) -> dict:
    row = c.execute(select(envelopes).where(envelopes.c.id == envelope_id, visible(actor))).mappings().first()
    if not row:
        raise HTTPException(404, 'Envelope not found.')
    return dict(row)


def settings_value(payload: dict | None, current: dict | None = None) -> dict:
    out = dict(current or {key: spec[0] for key, spec in SETTINGS.items()} | {'attach_completed': True})
    for key, (_, low, high) in SETTINGS.items():
        if payload and key in payload:
            try:
                value = int(payload[key])
            except (TypeError, ValueError):
                raise ValueError('Enter whole numbers for reminders and expiration.') from None
            if not low <= value <= high:
                raise ValueError(f'{key.replace("_", " ").capitalize()} must be between {low} and {high}.')
            out[key] = value
    if payload and 'attach_completed' in payload:
        out['attach_completed'] = payload['attach_completed'] is True
    return out


def new_ref(c) -> str:
    while True:
        ref = secrets.token_hex(4).upper() + '-' + secrets.token_hex(2).upper()
        if not c.execute(select(envelopes.c.id).where(envelopes.c.ref == ref)).first():
            return ref


def user_row(c, user_id: str) -> dict:
    return dict(c.execute(select(db.users).where(db.users.c.id == user_id)).mappings().one())


# ------------------------------------------------------------------ documents
def add_document(c, envelope: dict, name: str, raw: bytes, source: str, source_document_id=None, agreement=None) -> dict:
    sizes = esign_pdf.inspect(raw)
    position = c.execute(select(func.count()).select_from(documents).where(documents.c.envelope_id == envelope['id'])).scalar()
    if position >= 20:
        raise ValueError('An envelope can hold up to 20 documents.')
    row = dict(id=db.uid(), envelope_id=envelope['id'], position=position, name=clean(name, 'Document name', 200, True), source=source,
               source_document_id=source_document_id, agreement=agreement, pdf=raw, sha256=esign_pdf.sha256(raw), pages=len(sizes),
               page_sizes=sizes, created=now())
    c.execute(documents.insert().values(**row))
    return row


def create(c, actor: dict, payload: dict) -> dict:
    import crm_bridge
    contract = None
    if payload.get('contract_id'):
        contract = c.execute(select(db.contracts).where(db.contracts.c.id == str(payload['contract_id']), sec.scope(actor, db.contracts.c.owner_id),
                                                        sec.company_scope(actor, db.contracts.c.entity))).mappings().first()
        if not contract:
            raise HTTPException(404, 'Contract not found.')
        entity_name, client_id = contract['entity'], contract['client_id']
    else:
        entity_name = payload.get('entity') or ''
        client_id = payload.get('client_id') or None
    sec.ensure_company(actor, entity_name)
    client = None
    if client_id:
        client = c.execute(select(db.clients).where(db.clients.c.id == client_id, sec.scope(actor, db.clients.c.owner_id))).mappings().first()
        if not client:
            raise HTTPException(404, 'Client not found.')
    entity = get_entity(entity_name)
    t = now()
    envelope = dict(id=db.uid(), ref=new_ref(c), owner_id=actor['id'], entity=entity_name, client_id=client_id, contract_id=contract['id'] if contract else None,
                    subject='', message='', status='draft', template_name=None, settings=settings_value(None), version=1, created=t, updated=t,
                    expiry_warned=False, completion_attempts=0)
    c.execute(envelopes.insert().values(**envelope))
    box = mailer.load(c, entity_name)
    titles = []
    if contract:
        rows = c.execute(select(db.documents).where(db.documents.c.contract_id == contract['id'], db.documents.c.kind == 'pdf')
                         .order_by(db.documents.c.agreement)).mappings().all()
        order = {'baa': 0, 'services': 1, 'nda': 2}
        names = {'baa': 'Business Associate Agreement', 'services': 'Services Agreement', 'nda': 'Mutual NDA'}
        client_label = re.sub(r'[\\/:*?"<>|]+', '', client['name'] if client else '').strip()
        for row in sorted(rows, key=lambda r: order.get(r['agreement'], 9)):
            crm_bridge.check_download(c, row)
            title = names.get(row['agreement'], re.sub(r'\.pdf$', '', row['name'], flags=re.I))
            friendly = (f'{title} - {client_label}' if client_label else title)[:180] + '.pdf'
            add_document(c, envelope, friendly, bytes(row['body']), 'generated', row['id'], row['agreement'])
            titles.append(title)
        if not rows:
            raise HTTPException(400, 'This contract has no PDF documents to send.')
        _default_setup(c, envelope, contract, box)
    client_name = (client['name'] if client else '') or ''
    listed = ', '.join(titles[:-1]) + ' and ' + titles[-1] if len(titles) > 1 else (titles[0] if titles else 'documents')
    subject = f'Please sign: {listed}' + (f' - {client_name}' if client_name else '')
    signatory = (contract['payload'].get('client_signatory') if contract else '') or ''
    message = (f"Hello{' ' + signatory if signatory else ''},\n\nPlease review and sign the attached agreement{'s' if len(titles) != 1 else ''} from "
               f"{entity['name']}. If you have any questions, reply to this email" + (f" or write to {box['address']}" if box['address'] else '') + '.')
    c.execute(update(envelopes).where(envelopes.c.id == envelope['id']).values(subject=clean(payload.get('subject') or subject, 'Subject', 200),
                                                                               message=clean(payload.get('message') or message, 'Message', 4000)))
    record(c, envelope['id'], 'created', actor['name'], detail={'documents': titles})
    db.log(c, actor, 'esign.created', client_id, {'envelope': envelope['ref'], 'contract_id': envelope['contract_id']})
    return envelope


def _default_setup(c, envelope: dict, contract: dict, box: dict) -> None:
    """Default recipients and fields for agreements generated here: the client signs beside the client column."""
    payload = contract['payload']
    entity = get_entity(envelope['entity'])
    t = now()
    client = dict(id=db.uid(), envelope_id=envelope['id'], role='signer', role_name='Client', routing_order=1, name=(payload.get('client_signatory') or '')[:150],
                  email=(payload.get('client_email') or '')[:200], access_code=None, note='', status='created', auth_failures=0, delivery='pending',
                  reminders_sent=0, created=t)
    c.execute(recipients.insert().values(**client))
    company = None
    if payload.get('include_signature') is False:
        company = dict(client, id=db.uid(), role_name='Company signer', routing_order=2, name=entity['signatory'], email=box.get('signer_email') or '')
        c.execute(recipients.insert().values(**company))
    for doc in c.execute(select(documents).where(documents.c.envelope_id == envelope['id'])).mappings():
        anchors = esign_pdf.execution_anchors(bytes(doc['pdf']))
        if not anchors:
            continue
        side = anchors['client']
        plan = [('signature', side['signature'], True)]
        if side.get('name', {}).get('blank'):
            plan.append(('name', side['name'], True))
        if side.get('title', {}).get('blank'):
            plan.append(('title', side['title'], True))
        plan.append(('date_signed', side['date'], True))
        for kind, anchor, required in plan:
            add_field(c, envelope['id'], doc['id'], client['id'], anchors['page'], kind, esign_pdf.placement(anchor, kind), required)
        if company:
            add_field(c, envelope['id'], doc['id'], company['id'], anchors['page'], 'signature', esign_pdf.placement(anchors['company']['signature'], 'signature'), True)


def add_field(c, envelope_id, document_id, recipient_id, page, kind, box, required=True, label='', options=None):
    c.execute(fields.insert().values(id=db.uid(), envelope_id=envelope_id, document_id=document_id, recipient_id=recipient_id, page=page, kind=kind,
                                     x=box['x'], y=box['y'], w=box['w'], h=box['h'], required=required, label=label, options=options or {}))


# ------------------------------------------------------------------ draft editing
def save_draft(c, actor: dict, envelope: dict, payload: dict) -> None:
    if envelope['status'] not in ('draft', 'template'):
        raise HTTPException(409, 'Only a draft can be edited. Use Correct for an envelope in progress.')
    values = {'updated': now(), 'version': envelope['version'] + 1}
    if 'subject' in payload:
        values['subject'] = clean(payload['subject'], 'Subject', 200)
    if 'message' in payload:
        values['message'] = clean(payload['message'], 'Message', 4000)
    if 'settings' in payload:
        values['settings'] = settings_value(payload['settings'], envelope['settings'])
    if envelope['status'] == 'template' and 'template_name' in payload:
        values['template_name'] = clean(payload['template_name'], 'Template name', 150, True)
    if 'recipients' in payload:
        _replace_recipients(c, envelope, payload)
    c.execute(update(envelopes).where(envelopes.c.id == envelope['id']).values(**values))


def _replace_recipients(c, envelope: dict, payload: dict) -> None:
    items, field_items = payload.get('recipients'), payload.get('fields', [])
    if not isinstance(items, list) or len(items) > 20 or not isinstance(field_items, list) or len(field_items) > 3000:
        raise ValueError('An envelope can have up to 20 recipients.')
    existing = {r['id']: dict(r) for r in c.execute(select(recipients).where(recipients.c.envelope_id == envelope['id'])).mappings()}
    keys, rows = {}, []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError('Invalid recipient.')
        role = item.get('role')
        if role not in ROLES:
            raise ValueError('Choose what each recipient does.')
        try:
            order = int(item.get('routing_order', 1))
        except (TypeError, ValueError):
            order = 0
        if not 1 <= order <= 20:
            raise ValueError('Signing order must be between 1 and 20.')
        name = clean(item.get('name'), 'Recipient name', 150)
        address = clean(item.get('email'), 'Recipient email', 200)
        if address:
            address = mailer.valid_email(address)
        old = existing.get(item.get('id'))
        code = old['access_code'] if old else None
        if 'access_code' in item and item['access_code'] is not None:
            raw = str(item['access_code'])
            if raw and not 4 <= len(raw) <= 50:
                raise ValueError('An access code must have 4 to 50 characters.')
            code = sec.hasher.hash(raw) if raw else None
        row = dict(id=old['id'] if old else db.uid(), envelope_id=envelope['id'], role=role, role_name=clean(item.get('role_name'), 'Role', 60) or f'Recipient {i + 1}',
                   routing_order=order, name=name, email=address, access_code=code, note=clean(item.get('note'), 'Private message', 1000), status='created',
                   auth_failures=0, delivery='pending', reminders_sent=0, created=old['created'] if old else now())
        rows.append(row)
        keys[str(item.get('id') or '')] = row['id']
        if item.get('key'):
            keys[str(item['key'])] = row['id']
    sizes = {d['id']: d['page_sizes'] for d in c.execute(select(documents.c.id, documents.c.page_sizes).where(documents.c.envelope_id == envelope['id'])).mappings()}
    roles = {r['id']: r['role'] for r in rows}
    field_rows = []
    for item in field_items:
        if not isinstance(item, dict):
            raise ValueError('Invalid field.')
        rid = keys.get(str(item.get('recipient') or item.get('recipient_id') or ''))
        if not rid:
            raise ValueError('Each field must belong to a recipient.')
        if roles[rid] != 'signer':
            raise ValueError('Only recipients who sign can have fields.')
        kind = item.get('kind')
        if kind not in KINDS:
            raise ValueError('Unknown field type.')
        doc = item.get('document_id')
        if doc not in sizes:
            raise ValueError('A field refers to a document that is not in this envelope.')
        try:
            page = int(item.get('page'))
            x, y, w, h = (float(item.get(k)) for k in ('x', 'y', 'w', 'h'))
        except (TypeError, ValueError):
            raise ValueError('A field has an invalid position.') from None
        if not 1 <= page <= len(sizes[doc]):
            raise ValueError('A field is on a page that does not exist.')
        width, height = sizes[doc][page - 1][:2]
        if not (6 <= w <= width and 6 <= h <= height and -1 <= x <= width - w + 1 and -1 <= y <= height - h + 1):
            raise ValueError('A field is outside its page.')
        options = {}
        if kind == 'dropdown':
            choices = item.get('options', {}).get('choices') if isinstance(item.get('options'), dict) else None
            if not isinstance(choices, list) or not 1 <= len(choices) <= 50:
                raise ValueError('A dropdown needs between 1 and 50 choices.')
            options['choices'] = list(dict.fromkeys(clean(x, 'Choice', 100, True) for x in choices))
        field_rows.append(dict(id=db.uid(), envelope_id=envelope['id'], document_id=doc, recipient_id=rid, page=page, kind=kind, x=round(x, 2), y=round(y, 2),
                               w=round(w, 2), h=round(h, 2), required=item.get('required', kind != 'checkbox') is True, label=clean(item.get('label'), 'Field label', 100),
                               options=options))
    c.execute(delete(fields).where(fields.c.envelope_id == envelope['id']))
    c.execute(delete(tokens).where(tokens.c.recipient_id.in_(list(existing))))
    c.execute(delete(recipients).where(recipients.c.envelope_id == envelope['id']))
    for row in rows:
        c.execute(recipients.insert().values(**row))
    for row in field_rows:
        c.execute(fields.insert().values(**row))


# ------------------------------------------------------------------ sending and routing
def issue_token(c, recipient_id: str, via='email') -> str:
    token = secrets.token_urlsafe(32)
    c.execute(tokens.insert().values(id=digest(token), recipient_id=recipient_id, via=via, created=now(), expires=now() + 300 if via == 'portal' else None))
    return token


def sign_url(token: str) -> str:
    return f'{mailer.origin()}/sign/{token}'


def portal_url(envelope: dict) -> str:
    return f"{mailer.origin()}/?envelope={envelope['id']}"


def expires_label(envelope: dict) -> str:
    if not envelope.get('expires_at'):
        return ''
    tz = zoneinfo.ZoneInfo(get_entity(envelope['entity'])['timezone'])
    return dt.datetime.fromtimestamp(envelope['expires_at'], tz).strftime('%B %d, %Y').replace(' 0', ' ')


def recipient_email(c, envelope: dict, r: dict, kind: str) -> None:
    entity = get_entity(envelope['entity'])
    box = mailer.load(c, envelope['entity'])
    owner = user_row(c, envelope['owner_id'])
    docs = [d['name'] for d in c.execute(select(documents.c.name).where(documents.c.envelope_id == envelope['id']).order_by(documents.c.position)).mappings()]
    ctx = dict(entity=entity, box=box, subject=envelope['subject'], message=envelope['message'], note=r['note'], role=r['role'], recipient_name=r['name'] or r['email'],
               sender_name=owner['name'], sender_email=box['address'] or owner['email'], documents=docs, access_code=bool(r['access_code']),
               expires=expires_label(envelope), url=sign_url(issue_token(c, r['id'])))
    subject, body, html_body = mailer.compose(kind, ctx)
    mailer.enqueue(c, envelope['entity'], kind, [r['email']], subject, body, html_body, envelope_id=envelope['id'], recipient_id=r['id'])


def notify(c, envelope: dict, event: str, actor: str, detail: str = '', attach: bool = False) -> None:
    """Tell the sender and the company's compliance mailbox (as configured) about an envelope event."""
    box = mailer.load(c, envelope['entity'])
    if event not in box['notify'] or not mailer.ready(box):
        return
    owner = user_row(c, envelope['owner_id'])
    refs, attached = completion_attachments(c, envelope) if attach else ([], False)
    ctx = dict(entity=get_entity(envelope['entity']), box=box, subject=envelope['subject'], ref=envelope['ref'], actor=actor, detail=detail,
               url=portal_url(envelope), attached=attached)
    subject, body, html_body = mailer.compose('notify_' + event, ctx)
    to = [owner['email']] if owner['active'] else []
    mailer.enqueue(c, envelope['entity'], 'notify_' + event, to, subject, body, html_body, envelope_id=envelope['id'], bcc=[box['address']],
                   attachments=refs if attached else [])


def completion_attachments(c, envelope: dict) -> tuple[list[dict], bool]:
    if not envelope['settings'].get('attach_completed', True):
        return [], False
    rows = c.execute(select(documents.c.id, documents.c.name, func.length(documents.c.final_pdf)).where(documents.c.envelope_id == envelope['id'])
                     .order_by(documents.c.position)).all()
    size = sum(r[2] or 0 for r in rows) + len(envelope.get('certificate') or b'')
    if size > mailer.ATTACHMENT_LIMIT:
        return [], False
    return [{'kind': 'final', 'id': r[0], 'name': r[1]} for r in rows] + [{'kind': 'certificate', 'id': envelope['id']}], True


def send(c, actor: dict, envelope: dict, ip: str, ua: str) -> None:
    if envelope['status'] != 'draft':
        raise HTTPException(409, 'This envelope has already been sent.')
    docs = c.execute(select(documents.c.id).where(documents.c.envelope_id == envelope['id'])).all()
    rs = [dict(r) for r in c.execute(select(recipients).where(recipients.c.envelope_id == envelope['id'])).mappings()]
    fs = [dict(f) for f in c.execute(select(fields).where(fields.c.envelope_id == envelope['id'])).mappings()]
    problems = []
    if not envelope['subject'].strip():
        problems.append('Enter an email subject.')
    if not docs:
        problems.append('Add at least one document.')
    if not any(r['role'] != 'cc' for r in rs):
        problems.append('Add at least one recipient who signs or views.')
    for r in rs:
        who = r['name'] or r['role_name']
        if not r['name'] or not r['email']:
            problems.append(f'Enter the name and email of {who}.')
        if r['role'] == 'signer' and not any(f['recipient_id'] == r['id'] and f['kind'] == 'signature' for f in fs):
            problems.append(f'Add a signature field for {who}.')
    if problems:
        raise HTTPException(400, ' '.join(dict.fromkeys(problems)))
    try:
        mailer.transport(c, envelope['entity'])
    except mailer.MailboxNotReady as exc:
        raise HTTPException(409, str(exc)) from None
    t = now()
    expires = t + envelope['settings']['expire_days'] * 86400
    c.execute(update(envelopes).where(envelopes.c.id == envelope['id']).values(status='sent', sent_at=t, expires_at=expires, updated=t, version=envelope['version'] + 1))
    envelope.update(status='sent', sent_at=t, expires_at=expires)
    record(c, envelope['id'], 'sent', actor['name'], ip=ip, ua=ua, detail={'recipients': len(rs), 'documents': len(docs)})
    advance(c, envelope)
    db.log(c, actor, 'esign.sent', envelope['client_id'], {'envelope': envelope['ref'], 'subject': envelope['subject']})


def advance(c, envelope: dict) -> None:
    """Notify everyone whose turn has come; start completion when every signer and viewer is done."""
    rs = [dict(r) for r in c.execute(select(recipients).where(recipients.c.envelope_id == envelope['id'])).mappings()]
    pending = [r for r in rs if r['role'] != 'cc' and r['status'] not in DONE_STATUSES]
    if not pending:
        t = now()
        c.execute(update(envelopes).where(envelopes.c.id == envelope['id']).values(status='completing', completion_next=t, updated=t))
        envelope['status'] = 'completing'
        mailer.wake.set()
        return
    turn = min(r['routing_order'] for r in pending)
    t = now()
    for r in sorted(rs, key=lambda x: x['routing_order']):
        if r['status'] == 'created' and r['routing_order'] <= turn:
            c.execute(update(recipients).where(recipients.c.id == r['id']).values(status='sent', sent_at=t))
            record(c, envelope['id'], 'routed', 'TFSC Agreements', recipient_id=r['id'], detail={'to': r['email'], 'role': r['role']})
            recipient_email(c, envelope, dict(r, status='sent'), 'copy' if r['role'] == 'cc' else 'invite')


# ------------------------------------------------------------------ sender actions on an envelope in progress
def void(c, actor: dict, envelope: dict, reason: str, ip: str, ua: str) -> None:
    if envelope['status'] != 'sent':
        raise HTTPException(409, 'Only an envelope that is waiting for signatures can be voided.')
    reason = clean(reason, 'Reason for voiding', 500, True)
    t = now()
    c.execute(update(envelopes).where(envelopes.c.id == envelope['id']).values(status='voided', closed_at=t, close_reason=reason, updated=t, version=envelope['version'] + 1))
    record(c, envelope['id'], 'voided', actor['name'], ip=ip, ua=ua, detail={'reason': reason})
    _closing_emails(c, envelope, 'voided', reason, exclude=None)
    notify(c, dict(envelope, status='voided'), 'voided', actor['name'], f'Reason: {reason}')
    db.log(c, actor, 'esign.voided', envelope['client_id'], {'envelope': envelope['ref'], 'reason': reason})


def _closing_emails(c, envelope: dict, kind: str, reason: str, exclude: str | None) -> None:
    entity, box = get_entity(envelope['entity']), mailer.load(c, envelope['entity'])
    owner = user_row(c, envelope['owner_id'])
    for r in c.execute(select(recipients).where(recipients.c.envelope_id == envelope['id'], recipients.c.status != 'created')).mappings():
        if r['id'] == exclude:
            continue
        subject, body, html_body = mailer.compose('voided' if kind == 'voided' else 'declined_info', dict(
            entity=entity, box=box, subject=envelope['subject'], recipient_name=r['name'] or r['email'], reason=reason if kind == 'voided' else '', sender_name=owner['name']))
        mailer.enqueue(c, envelope['entity'], kind, [r['email']], subject, body, html_body, envelope_id=envelope['id'], recipient_id=r['id'])


def resend(c, actor: dict, envelope: dict, recipient_id: str | None) -> int:
    if envelope['status'] != 'sent':
        raise HTTPException(409, 'Only an envelope that is waiting for signatures can be resent.')
    count = 0
    for r in c.execute(select(recipients).where(recipients.c.envelope_id == envelope['id'], recipients.c.status.in_(OPEN_STATUSES), recipients.c.role != 'cc')).mappings():
        if recipient_id and r['id'] != recipient_id:
            continue
        recipient_email(c, envelope, dict(r), 'invite')
        record(c, envelope['id'], 'resent', actor['name'], recipient_id=r['id'], detail={'to': r['email']})
        count += 1
    if not count:
        raise HTTPException(400, 'Nobody is waiting to act on this envelope. Correct a recipient who could not be reached.')
    return count


def correct(c, actor: dict, envelope: dict, payload: dict, ip: str, ua: str) -> None:
    if envelope['status'] != 'sent':
        raise HTTPException(409, 'Only an envelope that is waiting for signatures can be corrected.')
    changed = []
    current = {r['id']: dict(r) for r in c.execute(select(recipients).where(recipients.c.envelope_id == envelope['id'])).mappings()}
    for item in payload.get('recipients', []) or []:
        r = current.get(item.get('id') if isinstance(item, dict) else None)
        if not r:
            raise ValueError('Unknown recipient.')
        if r['status'] in DONE_STATUSES + ('declined',):
            raise ValueError(f"{r['name']} has already finished and cannot be corrected.")
        values = {}
        if 'name' in item and clean(item['name'], 'Recipient name', 150, True) != r['name']:
            values['name'] = clean(item['name'], 'Recipient name', 150, True)
        if 'email' in item and mailer.valid_email(item['email']) != r['email']:
            values['email'] = mailer.valid_email(item['email'])
        if 'note' in item:
            values['note'] = clean(item['note'], 'Private message', 1000)
        if item.get('access_code') is not None:
            raw = str(item['access_code'])
            if raw and not 4 <= len(raw) <= 50:
                raise ValueError('An access code must have 4 to 50 characters.')
            values['access_code'] = sec.hasher.hash(raw) if raw else None
        if r['status'] == 'auth_failed' and item.get('unlock'):
            values.update(status='sent', auth_failures=0)
        if not values:
            continue
        if 'email' in values or 'access_code' in values or 'status' in values:
            c.execute(delete(tokens).where(tokens.c.recipient_id == r['id']))
            c.execute(delete(sign_sessions).where(sign_sessions.c.recipient_id == r['id']))
            values.setdefault('auth_failures', 0)
        if 'email' in values:
            values.update(delivery='pending', delivery_error=None)
        c.execute(update(recipients).where(recipients.c.id == r['id']).values(**values))
        changed.append({'recipient': r['id'], 'fields': sorted(k for k in values if k not in ('auth_failures', 'delivery', 'delivery_error'))})
        r.update(values)
        if r['status'] in OPEN_STATUSES or (r['role'] == 'cc' and r['status'] == 'sent'):
            recipient_email(c, envelope, r, 'corrected')
    env_values = {}
    if payload.get('expire_days') is not None:
        days = settings_value({'expire_days': payload['expire_days']})['expire_days']
        env_values.update(expires_at=now() + days * 86400, expiry_warned=False)
        changed.append({'expiration_days': days})
    if payload.get('settings'):
        settings = settings_value({k: v for k, v in payload['settings'].items() if k != 'expire_days'}, envelope['settings'])
        env_values['settings'] = settings
        changed.append({'settings': True})
    if not changed:
        raise HTTPException(400, 'Nothing was changed.')
    c.execute(update(envelopes).where(envelopes.c.id == envelope['id']).values(updated=now(), version=envelope['version'] + 1, **env_values))
    record(c, envelope['id'], 'corrected', actor['name'], ip=ip, ua=ua, detail={'changes': changed})


def delete_draft(c, envelope: dict) -> None:
    if envelope['status'] not in ('draft', 'template'):
        raise HTTPException(409, 'Only drafts and templates can be deleted. Void an envelope that was sent.')
    rid = [r[0] for r in c.execute(select(recipients.c.id).where(recipients.c.envelope_id == envelope['id']))]
    for statement in (delete(fields).where(fields.c.envelope_id == envelope['id']), delete(tokens).where(tokens.c.recipient_id.in_(rid)),
                      delete(sign_sessions).where(sign_sessions.c.recipient_id.in_(rid)), delete(recipients).where(recipients.c.envelope_id == envelope['id']),
                      delete(documents).where(documents.c.envelope_id == envelope['id']), delete(events).where(events.c.envelope_id == envelope['id']),
                      delete(outbox).where(outbox.c.envelope_id == envelope['id']), delete(envelopes).where(envelopes.c.id == envelope['id'])):
        c.execute(statement)


def copy(c, actor: dict, source: dict, *, as_template: str | None = None, client_id=None) -> dict:
    """New draft (or template) with the same documents, recipients and field layout, without any signing data."""
    sec.ensure_company(actor, source['entity'])
    t = now()
    target = dict(id=db.uid(), ref=new_ref(c), owner_id=actor['id'], entity=source['entity'], client_id=client_id if client_id is not None else (None if as_template else source['client_id']),
                  contract_id=None if as_template else source['contract_id'], subject=source['subject'], message=source['message'],
                  status='template' if as_template else 'draft', template_name=as_template, settings=settings_value(None, {k: source['settings'][k] for k in source['settings']}),
                  version=1, created=t, updated=t, expiry_warned=False, completion_attempts=0)
    c.execute(envelopes.insert().values(**target))
    doc_map, rcpt_map = {}, {}
    for d in c.execute(select(documents).where(documents.c.envelope_id == source['id']).order_by(documents.c.position)).mappings():
        new = dict(d, id=db.uid(), envelope_id=target['id'], source='template' if as_template else d['source'], final_pdf=None, final_sha256=None, created=t)
        c.execute(documents.insert().values(**new))
        doc_map[d['id']] = new['id']
    for r in c.execute(select(recipients).where(recipients.c.envelope_id == source['id'])).mappings():
        new = dict(id=db.uid(), envelope_id=target['id'], role=r['role'], role_name=r['role_name'], routing_order=r['routing_order'], name=r['name'], email=r['email'],
                   access_code=None, note=r['note'], status='created', auth_failures=0, delivery='pending', reminders_sent=0, created=t)
        c.execute(recipients.insert().values(**new))
        rcpt_map[r['id']] = new['id']
    for f in c.execute(select(fields).where(fields.c.envelope_id == source['id'])).mappings():
        c.execute(fields.insert().values(**dict(f, id=db.uid(), envelope_id=target['id'], document_id=doc_map[f['document_id']], recipient_id=rcpt_map[f['recipient_id']],
                                                value=None, filled_at=None)))
    record(c, target['id'], 'created', actor['name'], detail={'copied_from': source['ref'], 'template': bool(as_template)})
    return target


# ------------------------------------------------------------------ signing (recipient side)
def ersd(entity: dict, address: str) -> str:
    contact = address or 'the sender'
    return (f"ELECTRONIC RECORD AND SIGNATURE DISCLOSURE\n\n"
            f"From time to time, {entity['name']} (\"we\", \"us\") may be required by law to provide you with certain written notices or disclosures. "
            f"This disclosure describes the terms for receiving such notices, disclosures and documents electronically through TFSC Agreements, and for "
            f"signing documents electronically.\n\n"
            f"Paper copies. You may request a paper copy of any document we provide to you electronically, free of charge, by emailing {contact}. "
            f"You may also download and print the documents while you review them and after everyone has signed.\n\n"
            f"Withdrawing your consent. You may withdraw your consent to receive documents electronically at any time by choosing \"Decline to sign\" "
            f"on the signing page or by emailing {contact}. Withdrawing consent does not affect the validity of documents you signed electronically "
            f"before you withdrew it. If you withdraw consent, completing the transaction may take longer because paper documents must be exchanged.\n\n"
            f"Scope. Your consent applies to the notices, disclosures, authorizations, acknowledgements and documents provided to you through TFSC "
            f"Agreements for the transaction described in the email you received.\n\n"
            f"Updating your contact information. To change the email address at which you receive documents, email {contact}.\n\n"
            f"Hardware and software. You need a current version of a web browser (Chrome, Edge, Firefox or Safari) with JavaScript enabled, an email "
            f"account, and the ability to open, save or print PDF files.\n\n"
            f"Your consent. By checking the box, you confirm that you can access and read this disclosure, that you can print it or save it for your "
            f"records, and that you agree to receive notices, disclosures and documents electronically and to sign them electronically, with the same "
            f"effect as a handwritten signature.")


def _cookie_path(recipient_id: str) -> str:
    return f'/sign/r/{recipient_id}/'


def open_link(token: str, request: Request):
    ip = request.client.host if request.client else ''
    ua = request.headers.get('user-agent', '')[:300]
    with write_tx() as c:
        if not sec.rate_limit(c, 'sign-link:' + ip, 120):
            return _message_page('Too many attempts', 'Please wait a few minutes and open the link from your email again.', 429)
        row = c.execute(select(tokens).where(tokens.c.id == digest(token))).mappings().first() if re.fullmatch(r'[A-Za-z0-9_-]{30,60}', token or '') else None
        if not row or (row['expires'] and row['expires'] < now()):
            return _message_page('This link is not valid', 'The link may have been replaced by a newer email, or the sender corrected your details. '
                                 'Open the most recent email you received, or contact the sender.', 404)
        if row['via'] == 'portal':
            c.execute(delete(tokens).where(tokens.c.id == row['id']))
        r = dict(c.execute(select(recipients).where(recipients.c.id == row['recipient_id'])).mappings().one())
        envelope = dict(c.execute(select(envelopes).where(envelopes.c.id == r['envelope_id'])).mappings().one())
        if envelope['status'] == 'completed' and envelope['completed_at'] < now() - LINK_DAYS_AFTER_COMPLETION * 86400:
            return _message_page('This link has expired', f"Contact {mailer.load(c, envelope['entity'])['address'] or 'the sender'} for a copy of the completed documents.", 410)
        key = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        c.execute(delete(sign_sessions).where(sign_sessions.c.expires < now()))
        c.execute(sign_sessions.insert().values(id=digest(key), csrf=csrf, recipient_id=r['id'], verified=not r['access_code'] or row['via'] == 'portal',
                                                via=row['via'], expires=now() + SESSION_SECONDS))
        if row['via'] == 'portal':
            record(c, envelope['id'], 'portal_session', r['name'], recipient_id=r['id'], ip=ip, ua=ua)
    response = RedirectResponse(_cookie_path(r['id']), status_code=303)
    response.set_cookie('tfsc_sign', key, max_age=SESSION_SECONDS, path=_cookie_path(r['id']), httponly=True, secure=not db.LOCAL, samesite='lax')
    return response


def _message_page(title: str, body: str, status: int = 200):
    import html as h
    page = (db.APP / 'sign.html').read_text(encoding='utf-8').replace('__CSRF__', '').replace('__MESSAGE_TITLE__', h.escape(title)).replace('__MESSAGE_BODY__', h.escape(body))
    return HTMLResponse(page, status_code=status)


def signer_session(c, request: Request, recipient_id: str, write: bool = False, need_verified: bool = True):
    key = request.cookies.get('tfsc_sign', '')
    row = c.execute(select(sign_sessions).where(sign_sessions.c.id == digest(key), sign_sessions.c.recipient_id == recipient_id,
                                                sign_sessions.c.expires > now())).mappings().first() if key else None
    if not row:
        raise HTTPException(401, 'Your signing session has ended. Open the link from your email again.')
    if write and not secrets.compare_digest(request.headers.get('x-csrf-token', ''), row['csrf']):
        raise HTTPException(403, 'Reload the page and try again.')
    r = dict(c.execute(select(recipients).where(recipients.c.id == recipient_id)).mappings().one())
    envelope = lock(c, r['envelope_id']) if write else dict(c.execute(select(envelopes).where(envelopes.c.id == r['envelope_id'])).mappings().one())
    if need_verified and not row['verified']:
        raise HTTPException(403, 'Enter the access code first.')
    return dict(row), r, envelope


def _actor_info(request: Request) -> tuple[str, str]:
    return (request.client.host if request.client else ''), request.headers.get('user-agent', '')[:300]


def signer_state(c, session: dict, r: dict, envelope: dict, request: Request) -> dict:
    entity = get_entity(envelope['entity'])
    box = mailer.load(c, envelope['entity'])
    owner = user_row(c, envelope['owner_id'])
    docs = [dict(d) for d in c.execute(select(documents.c.id, documents.c.name, documents.c.pages, documents.c.page_sizes)
                                       .where(documents.c.envelope_id == envelope['id']).order_by(documents.c.position)).mappings()]
    status = envelope['status']
    can_act = status == 'sent' and r['status'] in OPEN_STATUSES and r['role'] != 'cc'
    out = dict(subject=envelope['subject'], message=envelope['message'], ref=envelope['ref'], envelope_status=status, sender=owner['name'],
               sender_email=box['address'] or owner['email'], company=entity['name'], company_short=entity['short'],
               recipient=dict(name=r['name'], email=r['email'], role=r['role'], status=r['status'], note=r['note']),
               verified=bool(session['verified']), consent=bool(r['consent_at']), ersd=ersd(entity, box['address']), can_act=can_act,
               adopted=dict(signature=_data_url(r['signature_png']), initials=_data_url(r['initials_png'])), csrf=session['csrf'],
               timezone=entity['timezone'], expires_at=envelope['expires_at'], role_label=ROLE_LABELS[r['role']])
    if not session['verified']:
        return out
    out['documents'] = docs
    if r['consent_at'] or r['role'] == 'cc' or not can_act:
        mine = [dict(f) for f in c.execute(select(fields).where(fields.c.recipient_id == r['id'])).mappings()]
        out['fields'] = [{k: f[k] for k in ('id', 'document_id', 'page', 'kind', 'x', 'y', 'w', 'h', 'required', 'label', 'options', 'value')} for f in mine]
        others = c.execute(select(fields.c.document_id, fields.c.page, fields.c.x, fields.c.y, fields.c.w, fields.c.h, fields.c.kind, recipients.c.name, recipients.c.status)
                           .join(recipients, recipients.c.id == fields.c.recipient_id).where(fields.c.envelope_id == envelope['id'], fields.c.recipient_id != r['id'])).mappings()
        out['others'] = [dict(o, done=o['status'] in DONE_STATUSES) for o in others]
    out['completed'] = status == 'completed'
    return out


def _data_url(png) -> str:
    return 'data:image/png;base64,' + base64.b64encode(bytes(png)).decode() if png else ''


def mark_delivered(c, r: dict, envelope: dict, request: Request) -> None:
    if r['status'] == 'sent' and envelope['status'] == 'sent':
        ip, ua = _actor_info(request)
        c.execute(update(recipients).where(recipients.c.id == r['id'], recipients.c.status == 'sent').values(status='delivered', delivered_at=now()))
        record(c, envelope['id'], 'delivered', r['name'], recipient_id=r['id'], ip=ip, ua=ua)
        if r['role'] != 'cc':
            notify(c, envelope, 'viewed', f"{r['name']} ({r['email']})")


def verify_code(c, session: dict, r: dict, envelope: dict, code: str, request: Request) -> HTTPException | None:
    """Check the access code. A failure is returned (not raised) so that the attempt is committed before reporting it."""
    ip, ua = _actor_info(request)
    locked = HTTPException(423, 'Access is locked after too many wrong access codes. Contact the sender to unlock it.')
    if r['status'] == 'auth_failed':
        return locked
    if not sec.rate_limit(c, 'sign-code-ip:' + ip, 30):
        return HTTPException(429, 'Too many attempts. Try again later.')
    if r['access_code'] and sec.verify(r['access_code'], str(code or '')[:60]):
        c.execute(update(sign_sessions).where(sign_sessions.c.id == session['id']).values(verified=True))
        c.execute(update(recipients).where(recipients.c.id == r['id']).values(auth_failures=0))
        record(c, envelope['id'], 'auth_passed', r['name'], recipient_id=r['id'], ip=ip, ua=ua)
        return None
    failures = r['auth_failures'] + 1
    record(c, envelope['id'], 'auth_failed', r['name'], recipient_id=r['id'], ip=ip, ua=ua, detail={'attempt': failures})
    if failures >= 5:
        c.execute(update(recipients).where(recipients.c.id == r['id']).values(auth_failures=failures, status='auth_failed'))
        record(c, envelope['id'], 'locked', 'TFSC Agreements', recipient_id=r['id'], ip=ip, ua=ua)
        notify(c, envelope, 'auth_failed', f"{r['name']} ({r['email']})", 'Correct the envelope to unlock access or give the recipient the right code.')
        return locked
    c.execute(update(recipients).where(recipients.c.id == r['id']).values(auth_failures=failures))
    return HTTPException(403, f'That access code is not correct. {5 - failures} attempt{"s" if 5 - failures != 1 else ""} left.')


def _active(r: dict, envelope: dict) -> None:
    if envelope['status'] in ('voided', 'declined', 'expired'):
        raise HTTPException(409, f"This envelope was {envelope['status']}. No action is needed.")
    if envelope['status'] != 'sent' or envelope['expires_at'] and envelope['expires_at'] < now():
        raise HTTPException(409, 'This envelope is no longer waiting for signatures.')
    if r['status'] not in OPEN_STATUSES:
        raise HTTPException(409, 'You have already completed your part.' if r['status'] in DONE_STATUSES else 'This document is not waiting for you.')


def consent(c, session: dict, r: dict, envelope: dict, request: Request) -> None:
    _active(r, envelope)
    if not r['consent_at']:
        ip, ua = _actor_info(request)
        c.execute(update(recipients).where(recipients.c.id == r['id']).values(consent_at=now()))
        record(c, envelope['id'], 'consent', r['name'], recipient_id=r['id'], ip=ip, ua=ua)


def clean_image(value: str, label: str) -> bytes:
    """Decode and re-encode a signature image (PNG), dropping metadata and anything that is not pixels."""
    from PIL import Image as PILImage
    try:
        raw = base64.b64decode(str(value).split(',', 1)[-1], validate=True)
    except (ValueError, TypeError):
        raise ValueError(f'The {label} image could not be read.') from None
    if not 50 <= len(raw) <= 1_500_000:
        raise ValueError(f'The {label} image is too large or empty.')
    try:
        with PILImage.open(io.BytesIO(raw)) as image:
            if image.format not in ('PNG', 'JPEG') or image.width > 3000 or image.height > 1500:
                raise ValueError(f'Use a PNG or JPEG {label} image of at most 3000 x 1500 pixels.')
            image = image.convert('RGBA')
            box = image.getbbox()
            if not box:
                raise ValueError(f'The {label} is empty.')
            image = image.crop(box)
            image.thumbnail((900, 300))
            out = io.BytesIO()
            image.save(out, format='PNG', optimize=True)
            return out.getvalue()
    except ValueError:
        raise
    except Exception:  # noqa: BLE001  (Pillow raises many kinds of errors for broken images)
        raise ValueError(f'The {label} image could not be read.') from None


def adopt(c, session: dict, r: dict, envelope: dict, payload: dict, request: Request) -> None:
    _active(r, envelope)
    if not r['consent_at']:
        raise HTTPException(403, 'Agree to use electronic records and signatures first.')
    kind = payload.get('kind')
    if kind not in ('typed', 'drawn', 'uploaded'):
        raise ValueError('Choose how to create your signature.')
    values = {'signature_png': clean_image(payload.get('signature', ''), 'signature'), 'signature_kind': kind}
    if payload.get('initials'):
        values['initials_png'] = clean_image(payload['initials'], 'initials')
    c.execute(update(recipients).where(recipients.c.id == r['id']).values(**values))
    ip, ua = _actor_info(request)
    record(c, envelope['id'], 'adopted', r['name'], recipient_id=r['id'], ip=ip, ua=ua,
           detail={'kind': kind, 'signature_sha256': hashlib.sha256(values['signature_png']).hexdigest()})


def date_signed(envelope: dict, at: float) -> str:
    return dt.datetime.fromtimestamp(at, zoneinfo.ZoneInfo(get_entity(envelope['entity'])['timezone'])).strftime('%m/%d/%Y')


def finish(c, session: dict, r: dict, envelope: dict, values: dict, request: Request) -> None:
    _active(r, envelope)
    if r['role'] != 'signer':
        raise HTTPException(400, 'Only signers finish by signing.')
    if not r['consent_at']:
        raise HTTPException(403, 'Agree to use electronic records and signatures first.')
    if not isinstance(values, dict):
        raise ValueError('Invalid field values.')
    t = now()
    mine = [dict(f) for f in c.execute(select(fields).where(fields.c.recipient_id == r['id'])).mappings()]
    missing = 0
    for f in mine:
        raw = values.get(f['id'])
        kind = f['kind']
        if kind == 'signature':
            value = 'signed' if raw and r['signature_png'] else None
        elif kind == 'initials':
            value = 'initialed' if raw and r['initials_png'] else None
        elif kind == 'date_signed':
            value = date_signed(envelope, t)
        elif kind == 'name':
            value = r['name']
        elif kind == 'email':
            value = r['email']
        elif kind == 'checkbox':
            value = 'true' if raw is True or raw == 'true' else 'false'
            if f['required'] and value != 'true':
                value = None
        elif kind == 'dropdown':
            value = raw if raw in f['options'].get('choices', []) else None
        else:
            value = clean(raw, f['label'] or kind.capitalize(), 500) or None
        if value is None:
            if f['required'] or kind in ('signature', 'initials') and raw:
                missing += 1
            continue
        c.execute(update(fields).where(fields.c.id == f['id']).values(value=value, filled_at=t))
    if missing:
        raise HTTPException(400, f"Complete the {missing} required field{'s' if missing != 1 else ''} before finishing.")
    ip, ua = _actor_info(request)
    method = 'TFSC Agreements account' if session['via'] == 'portal' else 'Email link' + (' and access code' if r['access_code'] else '')
    c.execute(update(recipients).where(recipients.c.id == r['id']).values(status='signed', signed_at=t, ip=ip, user_agent=ua, auth_method=method))
    record(c, envelope['id'], 'signed', r['name'], recipient_id=r['id'], ip=ip, ua=ua, at=t,
           detail={'fields': len(mine), 'signature_sha256': hashlib.sha256(r['signature_png'] or b'').hexdigest() if r['signature_png'] else ''})
    notify(c, envelope, 'signed', f"{r['name']} ({r['email']})")
    advance(c, envelope)


def acknowledge(c, session: dict, r: dict, envelope: dict, request: Request) -> None:
    _active(r, envelope)
    if r['role'] != 'viewer':
        raise HTTPException(400, 'Only recipients who need to view can acknowledge.')
    if not r['consent_at']:
        raise HTTPException(403, 'Agree to use electronic records first.')
    ip, ua = _actor_info(request)
    c.execute(update(recipients).where(recipients.c.id == r['id']).values(status='completed', signed_at=now(), ip=ip, user_agent=ua, auth_method='Email link'))
    record(c, envelope['id'], 'reviewed', r['name'], recipient_id=r['id'], ip=ip, ua=ua)
    advance(c, envelope)


def decline(c, session: dict, r: dict, envelope: dict, reason: str, request: Request) -> None:
    _active(r, envelope)
    if r['role'] == 'cc':
        raise HTTPException(400, 'You received a copy; there is nothing to decline.')
    reason = clean(reason, 'Reason for declining', 1000, True)
    ip, ua = _actor_info(request)
    t = now()
    c.execute(update(recipients).where(recipients.c.id == r['id']).values(status='declined', declined_at=t, decline_reason=reason, ip=ip, user_agent=ua))
    c.execute(update(envelopes).where(envelopes.c.id == envelope['id']).values(status='declined', closed_at=t, close_reason=reason, updated=t, version=envelope['version'] + 1))
    record(c, envelope['id'], 'declined', r['name'], recipient_id=r['id'], ip=ip, ua=ua, detail={'reason': reason})
    _closing_emails(c, envelope, 'declined', reason, exclude=r['id'])
    notify(c, dict(envelope, status='declined'), 'declined', f"{r['name']} ({r['email']})", f'Reason: {reason}')
    db.log(c, None, 'esign.declined', envelope['client_id'], {'envelope': envelope['ref'], 'by': r['email']})


# ------------------------------------------------------------------ completion (worker)
def complete_due() -> int:
    done = 0
    t = now()
    with db.engine.begin() as c:
        ids = [x[0] for x in c.execute(select(envelopes.c.id).where(envelopes.c.status == 'completing', envelopes.c.completion_next <= t,
                                                                     or_(envelopes.c.lease_until.is_(None), envelopes.c.lease_until < t)).limit(5))]
    for envelope_id in ids:
        with db.engine.begin() as c:
            claimed = c.execute(update(envelopes).where(envelopes.c.id == envelope_id, envelopes.c.status == 'completing',
                                                        or_(envelopes.c.lease_until.is_(None), envelopes.c.lease_until < t)).values(lease_until=t + 900))
        if claimed.rowcount != 1:
            continue
        try:
            finalize(envelope_id)
            done += 1
        except Exception as exc:  # noqa: BLE001
            log.exception('completing envelope %s failed', envelope_id)
            with db.engine.begin() as c:
                attempts = c.execute(select(envelopes.c.completion_attempts).where(envelopes.c.id == envelope_id)).scalar() + 1
                c.execute(update(envelopes).where(envelopes.c.id == envelope_id).values(
                    completion_attempts=attempts, completion_error=str(exc)[:1000], lease_until=None, completion_next=now() + min(3600, 60 * 2 ** attempts)))
    return done


def signature_id(envelope: dict, r: dict) -> str:
    return hashlib.sha256(f"{envelope['id']}:{r['id']}:{r.get('signed_at')}".encode()).hexdigest()


def finalize(envelope_id: str) -> None:
    with db.engine.connect() as c:
        envelope = dict(c.execute(select(envelopes).where(envelopes.c.id == envelope_id)).mappings().one())
        docs = [dict(d) for d in c.execute(select(documents).where(documents.c.envelope_id == envelope_id).order_by(documents.c.position)).mappings()]
        rs = [dict(r) for r in c.execute(select(recipients).where(recipients.c.envelope_id == envelope_id).order_by(recipients.c.routing_order, recipients.c.created)).mappings()]
        fs = [dict(f) for f in c.execute(select(fields).where(fields.c.envelope_id == envelope_id)).mappings()]
        sender = user_row(c, envelope['owner_id'])
        box = mailer.load(c, envelope['entity'])
    signers = {r['id']: dict(r, signature_id=signature_id(envelope, r)) for r in rs}
    finals = {}
    for d in docs:
        finals[d['id']] = esign_pdf.stamp(bytes(d['pdf']), envelope['ref'], [f for f in fs if f['document_id'] == d['id']], signers)
    t = now()
    entity = get_entity(envelope['entity'])
    with write_tx() as c:
        current = lock(c, envelope_id)
        if current['status'] != 'completing':
            return
        completed_event = build_event(c, envelope_id, 'completed', 'TFSC Agreements', at=t, detail={'documents': [{'name': d['name'], 'sha256': esign_pdf.sha256(finals[d['id']])} for d in docs]})
        trail = [dict(e) for e in c.execute(select(events).where(events.c.envelope_id == envelope_id).order_by(events.c.seq)).mappings()] + [completed_event]
        names = {r['id']: r['name'] for r in rs}
        for e in trail:
            e['label'] = LABELS.get(e['kind'], e['kind']) + (f" ({names[e['recipient_id']]})" if e.get('recipient_id') in names and e['kind'] in ('routed',) else '')
        cert = esign_pdf.certificate(dict(envelope, status='completed', completed_at=t), entity,
                                     [dict(d, final_sha256=esign_pdf.sha256(finals[d['id']])) for d in docs],
                                     [dict(r, signature_id=signers[r['id']]['signature_id']) for r in rs], trail, sender, ersd(entity, box['address']), mailer.origin())
        for d in docs:
            c.execute(update(documents).where(documents.c.id == d['id']).values(final_pdf=finals[d['id']], final_sha256=esign_pdf.sha256(finals[d['id']])))
        c.execute(update(envelopes).where(envelopes.c.id == envelope_id).values(status='completed', completed_at=t, updated=t, certificate=cert,
                                                                                certificate_sha256=esign_pdf.sha256(cert), lease_until=None, completion_error=None,
                                                                                version=current['version'] + 1))
        c.execute(events.insert().values(**{k: v for k, v in completed_event.items() if k != 'label'}))
        envelope = dict(current, status='completed', completed_at=t, certificate=cert)
        refs, attached = completion_attachments(c, envelope)
        docs_names = [d['name'] for d in docs]
        for r in rs:
            if r['status'] == 'declined':
                continue
            ctx = dict(entity=entity, box=box, subject=envelope['subject'], recipient_name=r['name'] or r['email'], documents=docs_names, attached=attached,
                       url=sign_url(issue_token(c, r['id'])))
            subject, body, html_body = mailer.compose('completed', ctx)
            mailer.enqueue(c, envelope['entity'], 'completed', [r['email']], subject, body, html_body, envelope_id=envelope_id, recipient_id=r['id'],
                           attachments=refs if attached else [])
        notify(c, envelope, 'completed', '', attach=True)
        db.log(c, None, 'esign.completed', envelope['client_id'], {'envelope': envelope['ref'], 'documents': [{'name': d['name'], 'sha256': esign_pdf.sha256(finals[d['id']])} for d in docs]})


# ------------------------------------------------------------------ reminders, expiration warnings and expiry (worker)
def _due(envelope: dict, pending: list[dict], t: float) -> bool:
    s = envelope['settings']
    if envelope['expires_at'] and envelope['expires_at'] <= t:
        return True
    if s.get('warn_days') and not envelope['expiry_warned'] and envelope['expires_at'] and envelope['expires_at'] - s['warn_days'] * 86400 <= t and pending:
        return True
    if s.get('reminder_every'):
        for r in pending:
            since = r['last_reminder_at'] or r['sent_at'] or envelope['sent_at']
            if since + (s['reminder_every'] if r['last_reminder_at'] else s['reminder_delay']) * 86400 <= t and r['reminders_sent'] < 12:
                return True
    return False


def sweep() -> int:
    t = now()
    count = 0
    with db.engine.connect() as c:
        open_envelopes = {e['id']: dict(e) for e in c.execute(select(envelopes.c.id, envelopes.c.settings, envelopes.c.expires_at, envelopes.c.expiry_warned,
                                                                     envelopes.c.sent_at).where(envelopes.c.status == 'sent')).mappings()}
        waiting: dict[str, list] = {}
        if open_envelopes:
            for r in c.execute(select(recipients.c.envelope_id, recipients.c.sent_at, recipients.c.last_reminder_at, recipients.c.reminders_sent)
                               .join(envelopes, envelopes.c.id == recipients.c.envelope_id)
                               .where(envelopes.c.status == 'sent', recipients.c.status.in_(OPEN_STATUSES), recipients.c.role != 'cc')).mappings():
                waiting.setdefault(r['envelope_id'], []).append(dict(r))
    due = [e for e in open_envelopes.values() if _due(e, waiting.get(e['id'], []), t)]
    for e in due:
        with write_tx() as c:
            envelope = lock(c, e['id'])
            if envelope['status'] != 'sent':
                continue
            if envelope['expires_at'] and envelope['expires_at'] <= t:
                c.execute(update(envelopes).where(envelopes.c.id == envelope['id']).values(status='expired', closed_at=t, close_reason='Expired', updated=t))
                record(c, envelope['id'], 'expired', 'TFSC Agreements')
                notify(c, dict(envelope, status='expired'), 'expired', '')
                db.log(c, None, 'esign.expired', envelope['client_id'], {'envelope': envelope['ref']})
                count += 1
                continue
            s = envelope['settings']
            pending = [dict(r) for r in c.execute(select(recipients).where(recipients.c.envelope_id == envelope['id'], recipients.c.status.in_(OPEN_STATUSES),
                                                                           recipients.c.role != 'cc')).mappings()]
            if s.get('warn_days') and not envelope['expiry_warned'] and envelope['expires_at'] and envelope['expires_at'] - s['warn_days'] * 86400 <= t:
                for r in pending:
                    recipient_email(c, envelope, r, 'expiring')
                    record(c, envelope['id'], 'expiring', 'TFSC Agreements', recipient_id=r['id'], detail={'to': r['email']})
                c.execute(update(envelopes).where(envelopes.c.id == envelope['id']).values(expiry_warned=True))
                count += 1
                continue
            if not s.get('reminder_every'):
                continue
            for r in pending:
                since = r['last_reminder_at'] or r['sent_at'] or envelope['sent_at']
                gap = (s['reminder_every'] if r['last_reminder_at'] else s['reminder_delay']) * 86400
                if since + gap <= t and r['reminders_sent'] < 12 and r['delivery'] != 'bounced':
                    recipient_email(c, envelope, r, 'reminder')
                    c.execute(update(recipients).where(recipients.c.id == r['id']).values(last_reminder_at=t, reminders_sent=r['reminders_sent'] + 1))
                    record(c, envelope['id'], 'reminder', 'TFSC Agreements', recipient_id=r['id'], detail={'to': r['email'], 'number': r['reminders_sent'] + 1})
                    count += 1
    return count


# ------------------------------------------------------------------ mail callbacks (bounces, failures, replies)
def _envelope_row(c, envelope_id: str) -> dict:
    return dict(c.execute(select(envelopes).where(envelopes.c.id == envelope_id)).mappings().one())


def mail_failed(c, row: dict, reason: str) -> None:
    if not row['envelope_id']:
        return
    envelope = lock(c, row['envelope_id'])
    to = ', '.join(row['to_addrs'])
    record(c, envelope['id'], 'mail_failed', 'TFSC Agreements', recipient_id=row['recipient_id'], detail={'to': to, 'kind': row['kind'], 'reason': reason[:300]})


def mail_bounced(c, row: dict, info: dict) -> None:
    envelope = lock(c, row['envelope_id'])
    to = ', '.join(row['to_addrs'])
    record(c, envelope['id'], 'bounced', 'TFSC Agreements', recipient_id=row['recipient_id'], detail={'to': to, 'kind': row['kind'], 'reason': info['snippet'][:300]})
    if row['recipient_id'] and not row['kind'].startswith('notify_'):
        notify(c, envelope, 'bounced', to, f"{info['snippet'][:300]}\nCorrect the recipient's email address on the envelope and it will be sent again.")


def mail_replied(c, row: dict, info: dict, files: list[str], skipped: list[str]) -> None:
    envelope = lock(c, row['envelope_id'])
    record(c, envelope['id'], 'replied', info['from_addr'], recipient_id=row['recipient_id'],
           detail={'from': info['from_addr'], 'subject': info['subject'][:200], 'attachments': files, 'not_saved': skipped})
    note = info['snippet'][:600] + (f"\n\nAttachments saved: {', '.join(files)}" if files else '') + (f"\nNot saved: {', '.join(skipped)}" if skipped else '')
    box = mailer.load(c, envelope['entity'])
    if 'replied' in box['notify'] and mailer.ready(box):
        owner = user_row(c, envelope['owner_id'])
        subject, body, html_body = mailer.compose('notify_replied', dict(entity=get_entity(envelope['entity']), box=box, subject=envelope['subject'], ref=envelope['ref'],
                                                                         actor=info['from_addr'], detail=note, url=portal_url(envelope)))
        if owner['active']:
            mailer.enqueue(c, envelope['entity'], 'notify_replied', [owner['email']], subject, body, html_body, envelope_id=envelope['id'])


# ------------------------------------------------------------------ views for the portal
def summary(c, rows: list[dict]) -> list[dict]:
    ids = [r['id'] for r in rows]
    people: dict[str, list] = {}
    if ids:
        for r in c.execute(select(recipients.c.envelope_id, recipients.c.name, recipients.c.email, recipients.c.role, recipients.c.status, recipients.c.delivery,
                                  recipients.c.routing_order).where(recipients.c.envelope_id.in_(ids)).order_by(recipients.c.routing_order)).mappings():
            people.setdefault(r['envelope_id'], []).append({k: r[k] for k in ('name', 'email', 'role', 'status', 'delivery', 'routing_order')})
    clients = {}
    client_ids = [r['client_id'] for r in rows if r['client_id']]
    if client_ids:
        clients = dict(c.execute(select(db.clients.c.id, db.clients.c.name).where(db.clients.c.id.in_(client_ids))).all())
    owners = dict(c.execute(select(db.users.c.id, db.users.c.name).where(db.users.c.id.in_({r['owner_id'] for r in rows} or {''}))).all())
    out = []
    for r in rows:
        ps = people.get(r['id'], [])
        acting = [p for p in ps if p['role'] != 'cc']
        out.append({k: r[k] for k in ('id', 'ref', 'subject', 'status', 'entity', 'client_id', 'sent_at', 'updated', 'completed_at', 'expires_at', 'template_name', 'created')}
                   | {'client': clients.get(r['client_id'], ''), 'sender': owners.get(r['owner_id'], ''), 'recipients': ps,
                      'progress': [sum(p['status'] in DONE_STATUSES for p in acting), len(acting)],
                      'attention': any(p['delivery'] in ('bounced', 'failed') or p['status'] == 'auth_failed' for p in ps)})
    return out


def detail(c, actor: dict, envelope: dict) -> dict:
    entity = get_entity(envelope['entity'])
    docs = [dict(d) for d in c.execute(select(documents.c.id, documents.c.name, documents.c.pages, documents.c.page_sizes, documents.c.sha256, documents.c.final_sha256,
                                              documents.c.source, documents.c.agreement).where(documents.c.envelope_id == envelope['id']).order_by(documents.c.position)).mappings()]
    bottom = entity['bottom'] / 20
    for d in docs:
        d['initials_spot'] = [esign_pdf.initials_box(size, bottom if d['source'] in ('generated',) or d['agreement'] else None) for size in d['page_sizes']]
    rs = [dict(r) for r in c.execute(select(recipients).where(recipients.c.envelope_id == envelope['id']).order_by(recipients.c.routing_order, recipients.c.created)).mappings()]
    people = []
    for r in rs:
        item = {k: r[k] for k in ('id', 'role', 'role_name', 'routing_order', 'name', 'email', 'note', 'status', 'sent_at', 'delivered_at', 'signed_at', 'declined_at',
                                  'decline_reason', 'delivery', 'delivery_error', 'reminders_sent', 'auth_method')}
        item['has_access_code'] = bool(r['access_code'])
        people.append(item)
    fs = [{k: f[k] for k in ('id', 'document_id', 'recipient_id', 'page', 'kind', 'x', 'y', 'w', 'h', 'required', 'label', 'options', 'value')}
          for f in c.execute(select(fields).where(fields.c.envelope_id == envelope['id'])).mappings()]
    trail = [dict(e) for e in c.execute(select(events).where(events.c.envelope_id == envelope['id']).order_by(events.c.seq)).mappings()]
    names = {r['id']: r['name'] for r in rs}
    history = [{'at': e['at'], 'kind': e['kind'], 'label': LABELS.get(e['kind'], e['kind']), 'actor': e['actor'], 'ip': e['ip'], 'recipient': names.get(e['recipient_id'], ''),
                'detail': e['detail']} for e in trail]
    mails = [{k: m[k] for k in ('kind', 'to_addrs', 'cc_addrs', 'bcc_addrs', 'state', 'last_error', 'sent_at', 'created', 'attempts')}
             for m in c.execute(select(outbox).where(outbox.c.envelope_id == envelope['id']).order_by(outbox.c.created)).mappings()]
    replies = []
    for m in c.execute(select(inbound).where(inbound.c.envelope_id == envelope['id']).order_by(inbound.c.received)).mappings():
        files = [dict(f) for f in c.execute(select(inbound_files.c.id, inbound_files.c.name, inbound_files.c.size, inbound_files.c.scan)
                                            .where(inbound_files.c.inbound_id == m['id'])).mappings()]
        replies.append({k: m[k] for k in ('id', 'kind', 'from_addr', 'subject', 'received', 'snippet')} | {'files': files})
    owner = user_row(c, envelope['owner_id'])
    client = c.execute(select(db.clients.c.name).where(db.clients.c.id == envelope['client_id'])).scalar() if envelope['client_id'] else ''
    box = mailer.load(c, envelope['entity'])
    mine = next((r for r in rs if r['email'].casefold() == actor['email'].casefold() and r['status'] in OPEN_STATUSES and envelope['status'] == 'sent' and r['role'] != 'cc'), None)
    return {k: envelope[k] for k in ('id', 'ref', 'entity', 'client_id', 'contract_id', 'subject', 'message', 'status', 'template_name', 'settings', 'version', 'created',
                                     'updated', 'sent_at', 'expires_at', 'completed_at', 'closed_at', 'close_reason', 'completion_error', 'certificate_sha256')} | {
        'client': client or '', 'sender': {'name': owner['name'], 'email': owner['email']}, 'documents': docs, 'recipients': people, 'fields': fs, 'history': history,
        'chain_ok': verify_chain(trail), 'mails': mails, 'replies': replies, 'entity_short': entity['short'],
        'mailbox': {'ready': mailer.ready(box), 'address': box['address'], 'signer_email': box['signer_email']},
        'can_sign_now': mine['id'] if mine else None, 'is_owner': envelope['owner_id'] == actor['id']}


def list_envelopes(c, actor: dict, box: str, q: str, page: int) -> dict:
    page = max(0, min(int(page or 0), 10000))
    if box == 'action':
        cond = and_(envelopes.c.status == 'sent', sec.company_scope(actor, envelopes.c.entity), envelopes.c.id.in_(
            select(recipients.c.envelope_id).where(func.lower(recipients.c.email) == actor['email'].lower(), recipients.c.status.in_(OPEN_STATUSES), recipients.c.role != 'cc')))
    else:
        statuses = {'waiting': ('sent', 'completing'), 'completed': ('completed',), 'drafts': ('draft',), 'declined': ('declined',), 'voided': ('voided',),
                    'expired': ('expired',), 'templates': ('template',)}.get(box)
        cond = visible(actor)
        cond = and_(cond, envelopes.c.status.in_(statuses)) if statuses else and_(cond, envelopes.c.status != 'template')
    if q:
        term = q.strip()[:100].lower()
        cond = and_(cond, or_(func.lower(envelopes.c.subject).contains(term, autoescape=True), func.lower(envelopes.c.ref).contains(term, autoescape=True),
                              func.lower(envelopes.c.template_name).contains(term, autoescape=True),
                              envelopes.c.id.in_(select(recipients.c.envelope_id).where(or_(func.lower(recipients.c.email).contains(term, autoescape=True),
                                                                                            func.lower(recipients.c.name).contains(term, autoescape=True))))))
    rows = [dict(r) for r in c.execute(select(envelopes.c.id, envelopes.c.ref, envelopes.c.subject, envelopes.c.status, envelopes.c.entity, envelopes.c.client_id,
                                              envelopes.c.owner_id, envelopes.c.sent_at, envelopes.c.updated, envelopes.c.completed_at, envelopes.c.expires_at,
                                              envelopes.c.template_name, envelopes.c.created)
                                       .where(cond).order_by(envelopes.c.updated.desc(), envelopes.c.id).limit(51).offset(page * 50)).mappings()]
    counts = {}
    for name, statuses in (('waiting', ('sent', 'completing')), ('drafts', ('draft',))):
        counts[name] = c.execute(select(func.count()).select_from(envelopes).where(visible(actor), envelopes.c.status.in_(statuses))).scalar()
    counts['action'] = c.execute(select(func.count()).select_from(envelopes).where(envelopes.c.status == 'sent', sec.company_scope(actor, envelopes.c.entity), envelopes.c.id.in_(
        select(recipients.c.envelope_id).where(func.lower(recipients.c.email) == actor['email'].lower(), recipients.c.status.in_(OPEN_STATUSES), recipients.c.role != 'cc')))).scalar()
    return {'envelopes': summary(c, rows[:50]), 'has_more': len(rows) > 50, 'page': page, 'counts': counts}


# ------------------------------------------------------------------ routes
def install(app) -> None:  # noqa: C901  (route table)
    from fastapi import Body

    def actor_of(request, c, write=False):
        return sec.get_actor(request, c)

    @app.get('/api/esign/envelopes')
    def api_list(request: Request, box: str = 'waiting', q: str = '', page: int = 0):
        with db.engine.connect() as c:
            return list_envelopes(c, actor_of(request, c), box, q, page)

    @app.post('/api/esign/envelopes')
    def api_create(request: Request, payload: dict = Body(...)):
        with write_tx() as c:
            a = actor_of(request, c)
            envelope = create(c, a, payload)
            return detail(c, a, get_envelope(c, a, envelope['id']))

    @app.get('/api/esign/envelopes/{envelope_id}')
    def api_detail(envelope_id: str, request: Request):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            return detail(c, a, get_envelope(c, a, envelope_id))

    @app.post('/api/esign/envelopes/{envelope_id}/draft')
    def api_save(envelope_id: str, request: Request, payload: dict = Body(...)):
        with write_tx() as c:
            a = actor_of(request, c)
            get_envelope(c, a, envelope_id)
            envelope = lock(c, envelope_id)
            if payload.get('version') is not None and payload['version'] != envelope['version']:
                raise HTTPException(409, 'Someone else changed this envelope. Reload it before saving.')
            save_draft(c, a, envelope, payload)
            return detail(c, a, get_envelope(c, a, envelope_id))

    @app.post('/api/esign/envelopes/{envelope_id}/documents')
    def api_upload(envelope_id: str, request: Request, payload: dict = Body(...)):
        try:
            raw = base64.b64decode(str(payload.get('data', '')), validate=True)
        except (ValueError, TypeError):
            raise HTTPException(400, 'The file could not be read.') from None
        if not 100 <= len(raw) <= 15_000_000:
            raise HTTPException(400, 'Upload a PDF smaller than 15 MB.')
        if not raw.startswith(b'%PDF'):
            raise HTTPException(400, 'Only PDF files can be uploaded. Save Word documents as PDF first.')
        name = clean(payload.get('name'), 'File name', 200, True)
        name = re.sub(r'[^\w .()&,-]', '_', name)
        if not name.lower().endswith('.pdf'):
            name += '.pdf'
        with write_tx() as c:
            a = actor_of(request, c)
            get_envelope(c, a, envelope_id)
            envelope = lock(c, envelope_id)
            if envelope['status'] not in ('draft', 'template'):
                raise HTTPException(409, 'Documents can be added only to a draft.')
            add_document(c, envelope, name, raw, 'upload')
            c.execute(update(envelopes).where(envelopes.c.id == envelope_id).values(updated=now(), version=envelope['version'] + 1))
            return detail(c, a, get_envelope(c, a, envelope_id))

    @app.post('/api/esign/envelopes/{envelope_id}/documents/{document_id}/remove')
    def api_remove_document(envelope_id: str, document_id: str, request: Request):
        with write_tx() as c:
            a = actor_of(request, c)
            get_envelope(c, a, envelope_id)
            envelope = lock(c, envelope_id)
            if envelope['status'] not in ('draft', 'template'):
                raise HTTPException(409, 'Documents can be removed only from a draft.')
            c.execute(delete(fields).where(fields.c.document_id == document_id, fields.c.envelope_id == envelope_id))
            if c.execute(delete(documents).where(documents.c.id == document_id, documents.c.envelope_id == envelope_id)).rowcount != 1:
                raise HTTPException(404, 'Document not found.')
            for i, d in enumerate(c.execute(select(documents.c.id).where(documents.c.envelope_id == envelope_id).order_by(documents.c.position)).all()):
                c.execute(update(documents).where(documents.c.id == d[0]).values(position=i))
            c.execute(update(envelopes).where(envelopes.c.id == envelope_id).values(updated=now(), version=envelope['version'] + 1))
            return detail(c, a, get_envelope(c, a, envelope_id))

    def _doc_for_sender(c, a, document_id):
        row = c.execute(select(documents).where(documents.c.id == document_id)).mappings().first()
        if not row:
            raise HTTPException(404, 'Document not found.')
        get_envelope(c, a, row['envelope_id'])
        return row

    @app.get('/api/esign/documents/{document_id}/page/{page}.png')
    def api_page(document_id: str, page: int, request: Request):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            row = _doc_for_sender(c, a, document_id)
            raw = bytes(row['pdf'])
            pages = row['pages']
        if not 1 <= page <= pages:
            raise HTTPException(404, 'Page not found.')
        png = esign_pdf.render_page(raw, page, PAGE_DPI, db.DATA / 'esign-cache' / document_id / f'{page}.png')
        # A document's pages never change (documents are immutable once added), so the browser may keep them.
        return Response(png, media_type='image/png', headers={'Cache-Control': 'private, max-age=86400, immutable'})

    @app.get('/api/esign/documents/{document_id}/file')
    def api_document(document_id: str, request: Request, final: int = 0):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            row = _doc_for_sender(c, a, document_id)
            body = row['final_pdf'] if final else row['pdf']
            if not body:
                raise HTTPException(404, 'The completed document is not ready yet.')
            name = row['name'] if not final else re.sub(r'\.pdf$', '', row['name'], flags=re.I) + ' (completed).pdf'
        return Response(bytes(body), media_type='application/pdf', headers={'Content-Disposition': 'attachment; filename="' + name.replace('"', '') + '"'})

    @app.get('/api/esign/envelopes/{envelope_id}/certificate')
    def api_certificate(envelope_id: str, request: Request):
        with db.engine.connect() as c:
            envelope = get_envelope(c, actor_of(request, c), envelope_id)
        if not envelope['certificate']:
            raise HTTPException(404, 'The certificate is created when the envelope is completed.')
        return Response(bytes(envelope['certificate']), media_type='application/pdf',
                        headers={'Content-Disposition': f'attachment; filename="Certificate of Completion {envelope["ref"]}.pdf"'})

    @app.get('/api/esign/envelopes/{envelope_id}/combined')
    def api_combined(envelope_id: str, request: Request):
        with db.engine.connect() as c:
            envelope = get_envelope(c, actor_of(request, c), envelope_id)
            parts = [bytes(r[0]) for r in c.execute(select(documents.c.final_pdf).where(documents.c.envelope_id == envelope_id).order_by(documents.c.position)) if r[0]]
        if envelope['status'] != 'completed' or not parts:
            raise HTTPException(404, 'The envelope is not completed yet.')
        return Response(esign_pdf.combine(parts + [bytes(envelope['certificate'])]), media_type='application/pdf',
                        headers={'Content-Disposition': f'attachment; filename="Completed {envelope["ref"]}.pdf"'})

    @app.get('/api/esign/attachments/{file_id}')
    def api_reply_file(file_id: str, request: Request):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            row = c.execute(select(inbound_files, inbound.c.envelope_id).join(inbound, inbound.c.id == inbound_files.c.inbound_id).where(inbound_files.c.id == file_id)).mappings().first()
            if not row or not row['envelope_id']:
                raise HTTPException(404, 'Attachment not found.')
            get_envelope(c, a, row['envelope_id'])
        return Response(bytes(row['body']), media_type='application/octet-stream',
                        headers={'Content-Disposition': 'attachment; filename="' + row['name'].replace('"', '') + '"', 'X-Content-Type-Options': 'nosniff'})

    def _action(envelope_id, request, fn, *args):
        ip, ua = _actor_info(request)
        with write_tx() as c:
            a = actor_of(request, c)
            get_envelope(c, a, envelope_id)
            envelope = lock(c, envelope_id)
            result = fn(c, a, envelope, *args, ip, ua) if fn in (send, void, correct) else fn(c, a, envelope, *args)
            return {'ok': True, 'result': result, 'envelope': detail(c, a, get_envelope(c, a, envelope_id))}

    @app.post('/api/esign/envelopes/{envelope_id}/send')
    def api_send(envelope_id: str, request: Request):
        return _action(envelope_id, request, send)

    @app.post('/api/esign/envelopes/{envelope_id}/void')
    def api_void(envelope_id: str, request: Request, payload: dict = Body(...)):
        return _action(envelope_id, request, void, payload.get('reason'))

    @app.post('/api/esign/envelopes/{envelope_id}/resend')
    def api_resend(envelope_id: str, request: Request, payload: dict = Body(default={})):
        return _action(envelope_id, request, resend, payload.get('recipient_id'))

    @app.post('/api/esign/envelopes/{envelope_id}/correct')
    def api_correct(envelope_id: str, request: Request, payload: dict = Body(...)):
        return _action(envelope_id, request, correct, payload)

    @app.post('/api/esign/envelopes/{envelope_id}/delete')
    def api_delete(envelope_id: str, request: Request):
        with write_tx() as c:
            a = actor_of(request, c)
            get_envelope(c, a, envelope_id)
            delete_draft(c, lock(c, envelope_id))
            return {'ok': True}

    @app.post('/api/esign/envelopes/{envelope_id}/copy')
    def api_copy(envelope_id: str, request: Request, payload: dict = Body(default={})):
        with write_tx() as c:
            a = actor_of(request, c)
            source = get_envelope(c, a, envelope_id)
            template_name = clean(payload.get('template_name'), 'Template name', 150) or None
            client_id = payload.get('client_id') or None
            if client_id and not c.execute(select(db.clients.c.id).where(db.clients.c.id == client_id, sec.scope(a, db.clients.c.owner_id))).first():
                raise HTTPException(404, 'Client not found.')
            target = copy(c, a, source, as_template=template_name, client_id=client_id if source['status'] == 'template' else None)
            return detail(c, a, get_envelope(c, a, target['id']))

    @app.post('/api/esign/envelopes/{envelope_id}/sign-now')
    def api_sign_now(envelope_id: str, request: Request):
        with write_tx() as c:
            a = actor_of(request, c)
            envelope = c.execute(select(envelopes).where(envelopes.c.id == envelope_id, envelopes.c.status == 'sent', sec.company_scope(a, envelopes.c.entity))).mappings().first()
            r = c.execute(select(recipients).where(recipients.c.envelope_id == envelope_id, func.lower(recipients.c.email) == a['email'].lower(),
                                                   recipients.c.status.in_(OPEN_STATUSES), recipients.c.role != 'cc').order_by(recipients.c.routing_order)).mappings().first() if envelope else None
            if not r:
                raise HTTPException(404, 'Nothing is waiting for your signature on this envelope.')
            return {'url': '/sign/' + issue_token(c, r['id'], 'portal')}

    # ---- company mailboxes
    @app.get('/api/esign/mailboxes')
    def api_mailboxes(request: Request):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            full = a['role'] == 'super_admin'
            names = [n for n in ENTITIES if full or n in a['companies']]
            return {'mailboxes': [mailer.public(mailer.load(c, n), full) for n in names], 'can_manage': full}

    def _super(c, request):
        a = actor_of(request, c)
        if a['role'] != 'super_admin':
            raise HTTPException(403, 'Only a Super Admin can manage company mailboxes.')
        return a

    @app.post('/api/esign/mailboxes/{index}')
    def api_mailbox_save(index: int, request: Request, payload: dict = Body(...)):
        with write_tx() as c:
            a = _super(c, request)
            name = list(ENTITIES)[index] if 0 <= index < len(ENTITIES) else ''
            try:
                box = mailer.save(c, a, name, payload)
            except mailer.secretbox.SecretKeyMissing as exc:
                raise HTTPException(503, str(exc)) from None
            return mailer.public(box, True)

    @app.post('/api/esign/mailboxes/{index}/test')
    def api_mailbox_test(index: int, request: Request, payload: dict = Body(...)):
        with db.engine.connect() as c:
            a = _super(c, request)
            name = list(ENTITIES)[index] if 0 <= index < len(ENTITIES) else ''
            get_entity(name)
            what = payload.get('what')
            result = {'at': now(), 'what': what}
            try:  # network work happens outside any write transaction
                if what == 'send':
                    mailer.send_test(c, name, payload.get('to') or a['email'])
                    result.update(ok=True, message=f"Test email sent to {payload.get('to') or a['email']}. Check that inbox (and its spam folder).")
                elif what == 'inbox':
                    count = mailer.check_inbox(c, name)
                    result.update(ok=True, message=f'Signed in to the inbox. It holds {count} message{"s" if count != 1 else ""}. Replies and bounces will be read from now on.')
                else:
                    raise ValueError('Choose a test.')
            except (ValueError, mailer.MailboxNotReady) as exc:
                result.update(ok=False, message=str(exc))
            except Exception as exc:  # noqa: BLE001  (the real reason is shown to the Super Admin)
                result.update(ok=False, message=mailer._describe(exc))
        with write_tx() as c:
            box = mailer.load(c, name)
            tests = dict(box['last_test'] or {})
            tests[what or 'unknown'] = result
            if c.execute(select(mailer.mailboxes.c.entity).where(mailer.mailboxes.c.entity == name)).first():
                c.execute(update(mailer.mailboxes).where(mailer.mailboxes.c.entity == name).values(last_test=tests))
            db.log(c, a, 'mailbox.tested', detail={'entity': name, 'what': what, 'ok': result['ok']})
            return result

    # ---- public verification of completed documents
    @app.get('/api/verify/{sha256}')
    def api_verify(sha256: str, request: Request):
        if not re.fullmatch(r'[0-9a-f]{64}', sha256):
            raise HTTPException(400, 'Invalid fingerprint.')
        with write_tx() as c:
            if not sec.rate_limit(c, 'verify:' + (request.client.host if request.client else ''), 60):
                raise HTTPException(429, 'Too many checks. Try again later.')
            doc = c.execute(select(documents.c.name, envelopes.c.ref, envelopes.c.entity, envelopes.c.completed_at).join(envelopes, envelopes.c.id == documents.c.envelope_id)
                            .where(documents.c.final_sha256 == sha256, envelopes.c.status == 'completed')).mappings().first()
            if not doc:
                cert = c.execute(select(envelopes.c.ref, envelopes.c.entity, envelopes.c.completed_at).where(envelopes.c.certificate_sha256 == sha256)).mappings().first()
                doc = dict(cert, name='Certificate of Completion') if cert else None
        if not doc:
            return {'match': False}
        return {'match': True, 'document': doc['name'], 'envelope': doc['ref'], 'company': doc['entity'], 'completed_at': doc['completed_at']}

    @app.get('/verify')
    def verify_page():
        return HTMLResponse((db.APP / 'verify.html').read_text(encoding='utf-8'))

    # ---- signer pages and API
    @app.get('/sign/{token}')
    def sign_link(token: str, request: Request):
        return open_link(token, request)

    @app.get('/sign/r/{recipient_id}/')
    def sign_page(recipient_id: str, request: Request):
        with db.engine.connect() as c:
            try:
                session, r, envelope = signer_session(c, request, recipient_id, need_verified=False)
            except HTTPException:
                return _message_page('Open the link from your email', 'For your security, documents open only from the link in the email you received.', 401)
        page = (db.APP / 'sign.html').read_text(encoding='utf-8').replace('__CSRF__', session['csrf']).replace('__MESSAGE_TITLE__', '').replace('__MESSAGE_BODY__', '')
        return HTMLResponse(page)

    @app.get('/sign/r/{recipient_id}/api/state')
    def sign_state(recipient_id: str, request: Request):
        with write_tx() as c:
            session, r, envelope = signer_session(c, request, recipient_id, need_verified=False)
            if session['verified']:
                mark_delivered(c, r, envelope, request)
                r = dict(c.execute(select(recipients).where(recipients.c.id == recipient_id)).mappings().one())
            return signer_state(c, session, r, envelope, request)

    def _sign_write(recipient_id, request, fn, *args, need_verified=True):
        with write_tx() as c:
            session, r, envelope = signer_session(c, request, recipient_id, write=True, need_verified=need_verified)
            failure = fn(c, session, r, envelope, *args, request)
            r = dict(c.execute(select(recipients).where(recipients.c.id == recipient_id)).mappings().one())
            envelope = dict(c.execute(select(envelopes).where(envelopes.c.id == r['envelope_id'])).mappings().one())
            session = dict(c.execute(select(sign_sessions).where(sign_sessions.c.id == session['id'])).mappings().one())
            state = signer_state(c, session, r, envelope, request)
        if failure is not None:
            raise failure
        return state

    @app.post('/sign/r/{recipient_id}/api/verify')
    def sign_verify(recipient_id: str, request: Request, payload: dict = Body(...)):
        return _sign_write(recipient_id, request, verify_code, payload.get('code', ''), need_verified=False)

    @app.post('/sign/r/{recipient_id}/api/consent')
    def sign_consent(recipient_id: str, request: Request):
        return _sign_write(recipient_id, request, consent)

    @app.post('/sign/r/{recipient_id}/api/adopt')
    def sign_adopt(recipient_id: str, request: Request, payload: dict = Body(...)):
        return _sign_write(recipient_id, request, adopt, payload)

    @app.post('/sign/r/{recipient_id}/api/finish')
    def sign_finish(recipient_id: str, request: Request, payload: dict = Body(...)):
        return _sign_write(recipient_id, request, finish, payload.get('values', {}))

    @app.post('/sign/r/{recipient_id}/api/acknowledge')
    def sign_ack(recipient_id: str, request: Request):
        return _sign_write(recipient_id, request, acknowledge)

    @app.post('/sign/r/{recipient_id}/api/decline')
    def sign_decline(recipient_id: str, request: Request, payload: dict = Body(...)):
        return _sign_write(recipient_id, request, decline, payload.get('reason', ''))

    def _signer_doc(c, request, recipient_id, document_id):
        session, r, envelope = signer_session(c, request, recipient_id)
        row = c.execute(select(documents).where(documents.c.id == document_id, documents.c.envelope_id == envelope['id'])).mappings().first()
        if not row:
            raise HTTPException(404, 'Document not found.')
        if r['role'] != 'cc' and r['status'] in OPEN_STATUSES and envelope['status'] == 'sent' and not r['consent_at']:
            raise HTTPException(403, 'Agree to use electronic records and signatures first.')
        return session, r, envelope, row

    @app.get('/sign/r/{recipient_id}/page/{document_id}/{page}.png')
    def sign_page_image(recipient_id: str, document_id: str, page: int, request: Request):
        with db.engine.connect() as c:
            _, _, _, row = _signer_doc(c, request, recipient_id, document_id)
            raw, pages = bytes(row['pdf']), row['pages']
        if not 1 <= page <= pages:
            raise HTTPException(404, 'Page not found.')
        png = esign_pdf.render_page(raw, page, PAGE_DPI, db.DATA / 'esign-cache' / document_id / f'{page}.png')
        return Response(png, media_type='image/png', headers={'Cache-Control': 'private, max-age=86400, immutable'})

    @app.get('/sign/r/{recipient_id}/document/{document_id}')
    def sign_document(recipient_id: str, document_id: str, request: Request):
        with db.engine.connect() as c:
            _, _, envelope, row = _signer_doc(c, request, recipient_id, document_id)
            completed = envelope['status'] == 'completed' and row['final_pdf']
            body = bytes(row['final_pdf'] if completed else row['pdf'])
        name = re.sub(r'\.pdf$', '', row['name'], flags=re.I) + (' (completed)' if completed else '') + '.pdf'
        return Response(body, media_type='application/pdf', headers={'Content-Disposition': 'attachment; filename="' + name.replace('"', '') + '"'})

    @app.get('/sign/r/{recipient_id}/completed')
    def sign_completed(recipient_id: str, request: Request):
        with db.engine.connect() as c:
            _, r, envelope = signer_session(c, request, recipient_id)
            if envelope['status'] != 'completed' or r['status'] == 'declined':
                raise HTTPException(404, 'The documents are not completed yet.')
            parts = [bytes(x[0]) for x in c.execute(select(documents.c.final_pdf).where(documents.c.envelope_id == envelope['id']).order_by(documents.c.position))]
        return Response(esign_pdf.combine(parts + [bytes(envelope['certificate'])]), media_type='application/pdf',
                        headers={'Content-Disposition': f'attachment; filename="Completed {envelope["ref"]}.pdf"'})

    @app.exception_handler(mailer.secretbox.SecretKeyMissing)
    async def _missing_key(request, exc):
        return JSONResponse({'error': str(exc)}, 503)

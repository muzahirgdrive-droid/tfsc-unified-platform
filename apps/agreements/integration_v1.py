"""TFS Agreements Integration API v1: the read-only feed for TFS Onboarding and Client Success (OCS).

The contract is frozen (packages/integration-contract/agreements-v1/: four JSON Schemas and a worked example). This
module is the only code that knows it. It reads TFS Agreements' tables and writes the v1 shape field by field; it never
returns a row or an internal payload as it is, so a later rename inside TFS Agreements can never leak into the feed.

What is published (04 §3): every client that is not a test client and has at least one signed, non-test agreement:
  * portal_signed: a completed, non-test envelope with a client; each of its documents (not signer attachments) is one
    agreement;
  * filed_past: an agreement filed on the client's record (client_agreements) with a signed date or a signed copy.
Each change is the complete current picture of one client (client.snapshot), or the notice that a client no longer
qualifies (client.withdrawn).

The publisher finds changes by reading the tables, never by being called from business code, so later work in TFS
Agreements cannot forget it: every 15 seconds (the worker's sweep lane) it reads the `updated` columns of clients,
esign_envelopes, client_agreements and client_contacts, and the activity log (which records test marks, Trash, restore
and purge, none of which touch an `updated` column), later than its watermark minus an overlap. Once a day, and when a
Super Admin asks for it, it rebuilds every client that has or had a signed agreement. A client whose picture did not
change (same hash) is never published again. It does nothing at all until the first token has been created, so an
installation that is never connected behaves exactly as before.

Routes (all GET, bearer token, JSON unless a file): /api/integration/v1/meta, /changes, /clients/{client_ref},
/documents/{document_ref}, /catalog. Admin › Integrations (Super Admin): tokens, feed position, "publish everything again".
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import Body, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import JSON, BigInteger, Boolean, Column, DateTime, Index, String, Table, UniqueConstraint, and_, func, or_, select, text

import database as db
import security as sec
from esign_db import client_agreements, client_contacts, client_files, envelopes, fields, recipients
from esign_db import documents as esign_documents

log = logging.getLogger('tfsc.integration')

CONTRACT = 'tfsc.agreements.integration/1'
CONTRACT_REVISION = 1
RELEASE = '5.3'
PREFIX = '/api/integration/v1'
LOCK_KEY = 714872014              # PostgreSQL advisory lock: one publisher at a time
EVERY = 15                        # seconds between publisher runs (the worker's sweep lane)
OVERLAP = 660.0                   # seconds read again each run: longer than any transaction that writes these tables may take
SWEEP_EVERY = 86400               # the full sweep runs once a day
KEEP_SNAPSHOTS = 30 * 86400       # older changes are deleted, except each client's newest
KEEP_WITHDRAWN = 365 * 86400
FAILED_LIMIT = 30                 # failed token attempts per address per 15 minutes before 429
MAX_LIMIT = 200
LETTERS = 'ABCDE'
QUIET_ACTIONS = ('%.viewed', '%_downloaded', '%_exported')   # activity that never changes what is published

# ------------------------------------------------------------------ tables (created by migration 0014_integration_feed)
feed = Table('integration_feed', db.meta,
             Column('seq', BigInteger, primary_key=True, autoincrement=False),
             Column('client_id', String(32), nullable=False),
             Column('kind', String(20), nullable=False),                  # client.snapshot | client.withdrawn
             Column('snapshot', JSON, nullable=True),
             Column('snapshot_hash', String(64), nullable=True),
             Column('published_at', DateTime(timezone=True), nullable=False))
Index('ix_integration_feed_client', feed.c.client_id, feed.c.seq)
published_state = Table('integration_clients', db.meta,
                        Column('client_id', String(32), primary_key=True),
                        Column('snapshot_hash', String(64), nullable=False),
                        Column('last_seq', BigInteger, nullable=False),
                        Column('published', Boolean, nullable=False),
                        Column('checked_at', DateTime(timezone=True), nullable=True))
tokens = Table('integration_tokens', db.meta,
               Column('id', String(32), primary_key=True),
               Column('name', String(100), nullable=False),
               Column('token_hash', String(64), nullable=False),
               Column('created', DateTime(timezone=True), nullable=False),
               Column('created_by', String(32), nullable=True),
               Column('last_used', DateTime(timezone=True), nullable=True),
               Column('last_ip', String(64), nullable=True),
               Column('revoked', DateTime(timezone=True), nullable=True),
               UniqueConstraint('token_hash', name='uq_integration_token_hash'))
# The publisher reads these to find what changed. On PostgreSQL they are built CONCURRENTLY by ensure_indexes().
SOURCE_INDEXES = (Index('ix_esign_env_updated', envelopes.c.updated), Index('ix_clients_updated', db.clients.c.updated),
                  Index('ix_client_agreements_updated', client_agreements.c.updated), Index('ix_client_contacts_updated', client_contacts.c.updated))

SETTING_INSTANCE = 'integration_instance_id'
SETTING_WATERMARK = 'integration_watermark'
SETTING_STATE = 'integration_state'          # last run, last sweep, sweep request, clients to retry


# ------------------------------------------------------------------ the two hashes of the contract
def snapshot_hash(snapshot: dict) -> str:
    """SHA-256 of the canonical JSON of the snapshot without snapshot_hash and generated_at (OCS compares it byte for byte)."""
    body = {k: v for k, v in snapshot.items() if k not in ('snapshot_hash', 'generated_at')}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()


def state_digest(published: list[tuple[str, str]]) -> str:
    """(client_ref, snapshot_hash) of every published client, sorted, one 'ref:hash' line each."""
    return hashlib.sha256('\n'.join(f'{ref}:{h}' for ref, h in sorted(published)).encode('utf-8')).hexdigest()


# ------------------------------------------------------------------ small helpers
def iso(moment) -> str | None:
    """RFC 3339 UTC, whole seconds: from an epoch float or a datetime."""
    if moment is None or moment == '':
        return None
    if isinstance(moment, dt.datetime):
        moment = moment if moment.tzinfo else moment.replace(tzinfo=dt.UTC)
        return moment.astimezone(dt.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    return dt.datetime.fromtimestamp(float(moment), dt.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _text(value, limit: int) -> str:
    return str(value or '').strip()[:limit]


def _day(value) -> str | None:
    value = str(value or '').strip()
    return value if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) else None


def _int(value) -> int | None:
    try:
        return int(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _decimal(value) -> Decimal | None:
    if value in (None, ''):
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _plain(value) -> str | None:
    d = _decimal(value)
    if d is None:
        return None
    s = format(d.normalize(), 'f')
    return '0' if s in ('-0', '') else s


def _money(value) -> str | None:
    d = _decimal(value)
    return None if d is None else str(d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def _setting(c, key, default=None):
    value = c.execute(select(db.settings.c.value).where(db.settings.c.key == key)).scalar()
    return default if value is None else value


def _put(c, key, value) -> None:
    if not c.execute(db.settings.update().where(db.settings.c.key == key).values(value=value)).rowcount:
        c.execute(db.settings.insert().values(key=key, value=value))


def instance_id(c) -> str:
    value = _setting(c, SETTING_INSTANCE)
    if not value:   # written by migration 0014; only a database that lost the row gets a new one
        import uuid
        value = str(uuid.uuid4())
        _put(c, SETTING_INSTANCE, value)
    return str(value)


def company_of(name: str) -> dict:
    """The contracting company as the contract names it: its stable key and its legal name."""
    from entities import ENTITIES
    name = str(name or '')
    try:
        key = ENTITIES[name]['key']
    except KeyError:
        key = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_') or 'unknown'
    return {'key': str(key), 'legal_name': name}


# ------------------------------------------------------------------ services and prices (04 §3.3)
def _summary(record: dict) -> str:
    """The price in words: the same function that words it on the agreement."""
    import revised_content
    try:
        return re.sub(r'\s+', ' ', revised_content.fee_description(record)).strip() or 'As stated in the signed agreement.'
    except Exception:  # noqa: BLE001  (an agreement generated by an old release with an incomplete price)
        return 'As stated in the signed agreement.'


def pricing_of(record: dict) -> dict:
    """One pricing option in the v1 shape. `record` is the service as the agreement printed it (catalogue base + payload)."""
    model = str(record.get('model') or '')
    out: dict = {'model': model, 'summary': _summary(record)}
    if record.get('model_def'):
        values = record.get('values') if isinstance(record.get('values'), dict) else {}
        out.update(model=f'custom:{model}', rate=_plain(record.get('rate')) if _decimal(record.get('rate')) else None, rate_unit='',
                   values={str(k): str(v) for k, v in sorted(values.items()) if v not in (None, '')})
        return out
    if model == 'percentage':
        out.update(rate=_plain(record.get('rate')), rate_unit='percent')
    elif model in ('fte', 'pte'):
        out.update(rate=_money(record.get('rate')), rate_unit='per_month', quantity=_plain(record.get('quantity') or 1),
                   weekly_hours=_plain(record.get('hours') if record.get('hours') not in (None, '') else (40 if model == 'fte' else 20)))
    elif model == 'unit':
        out.update(rate=_money(record.get('rate')), rate_unit='per_unit', quantity=_plain(record.get('quantity') or 1),
                   unit=_text(record.get('unit') or 'completed case', 100))
    elif model in ('fixed', 'consultancy'):
        out.update(rate=_money(record.get('rate')), rate_unit='per_month')
    elif model == 'rate_card':
        out.update(rate=_money(record.get('government_rate')), rate_unit='per_unit', government_rate=_money(record.get('government_rate')),
                   commercial_rate=_money(record.get('commercial_rate')))
    elif model == 'one_time':
        out.update(rate=_money(record.get('rate')), rate_unit='flat', quantity=_plain(record.get('quantity') or 1))
    elif model == 'two_installments':
        out.update(rate=_money(record.get('rate')), rate_unit='flat', quantity=_plain(record.get('quantity') or 1),
                   installment_percent=_plain(record.get('installment_percent') if record.get('installment_percent') not in (None, '') else 50),
                   milestones=[_text(record.get('milestone1') or 'execution and commencement', 300),
                               _text(record.get('milestone2') or 'delivery of the agreed final work product or completion milestone', 300)])
    else:
        out.update(rate=_plain(record.get('rate')), rate_unit='')
    return out


def services_of(payload: dict, chosen: dict[str, str]) -> list[dict]:
    """The services of a Services Agreement (or a custom agreement that carries services), as signed.
    chosen: {service name as printed: option letter the client chose}."""
    import contract_content
    import revised_content
    out = []
    items = [(s, False) for s in payload.get('services') or [] if isinstance(s, dict)]
    items += [(s, True) for s in payload.get('custom_services') or [] if isinstance(s, dict)]
    for s, custom in items:
        record = contract_content._service_record(s)
        try:
            name = revised_content.service_name(record)
        except Exception:  # noqa: BLE001
            name = record.get('name') or record.get('id') or 'Service'
        category = record.get('category') if record.get('category') in ('recurring', 'on_demand') else ''
        service = {'service_id': _text(record.get('id') or ('custom_' + hashlib.sha256(str(name).encode()).hexdigest()[:10]), 80),
                   'name': _text(name, 200), 'is_custom': bool(custom or str(record.get('id') or '').startswith('custom_')),
                   'category': category, 'details': str(record.get('details') or '').strip()}
        options = [o for o in (s.get('options') or []) if isinstance(o, dict)]
        if len(options) > 1:
            priced = [pricing_of(dict(record, **o)) for o in options[:len(LETTERS)]]
            service['options'] = [{'letter': LETTERS[i], 'pricing': p} for i, p in enumerate(priced)]
            letter = chosen.get(service['name'])
            index = LETTERS.find(letter) if letter else -1
            if 0 <= index < len(priced):
                service['pricing'], service['selected_option'] = priced[index], letter
            else:
                service['pricing'], service['selected_option'] = priced[0], None
        else:
            service['pricing'], service['selected_option'] = pricing_of(record), None
        out.append(service)
    return out


def _terms(payload: dict) -> dict:
    return {'notice_days': _int(payload.get('notice_days')), 'payment_days': _int(payload.get('payment_days')),
            'cure_days': _int(payload.get('cure_days')), 'special_terms': str(payload.get('special_terms') or '').strip()}


# ------------------------------------------------------------------ the snapshot of one client (04 §3)
def _portal_agreements(c, client_id: str) -> list[dict]:
    import client_records
    import server
    rows = [dict(r) for r in c.execute(
        select(envelopes.c.id, envelopes.c.ref, envelopes.c.entity, envelopes.c.contract_id, envelopes.c.completed_at, envelopes.c.certificate_sha256,
               func.length(envelopes.c.certificate).label('certificate_size'))
        .where(envelopes.c.client_id == client_id, envelopes.c.status == 'completed', envelopes.c.test.is_(False))
        .order_by(envelopes.c.completed_at, envelopes.c.id)).mappings()]
    if not rows:
        return []
    contract_ids = [e['contract_id'] for e in rows if e['contract_id']]
    contracts = {}
    if contract_ids:
        contracts = {r['id']: dict(r) for r in c.execute(select(db.contracts.c.id, db.contracts.c.payload, db.contracts.c.test)
                                                         .where(db.contracts.c.id.in_(contract_ids))).mappings()}
    # a test agreement sent for signature stays a test, even if its envelope was not marked
    rows = [e for e in rows if not (e['contract_id'] and contracts.get(e['contract_id'], {}).get('test'))]
    if not rows:
        return []
    ids = [e['id'] for e in rows]
    docs: dict[str, list] = {}
    for d in c.execute(select(esign_documents.c.id, esign_documents.c.envelope_id, esign_documents.c.name, esign_documents.c.source, esign_documents.c.agreement,
                              esign_documents.c.pages, esign_documents.c.final_sha256, func.length(esign_documents.c.final_pdf).label('final_size'))
                       .where(esign_documents.c.envelope_id.in_(ids), esign_documents.c.source != 'attachment')
                       .order_by(esign_documents.c.envelope_id, esign_documents.c.position)).mappings():
        docs.setdefault(d['envelope_id'], []).append(dict(d))
    people: dict[str, list] = {}
    for r in c.execute(select(recipients.c.id, recipients.c.envelope_id, recipients.c.name, recipients.c.email, recipients.c.role_name, recipients.c.signed_at,
                              recipients.c.routing_order, recipients.c.created)
                       .where(recipients.c.envelope_id.in_(ids), recipients.c.role.in_(('signer', 'in_person')), recipients.c.signed_at.isnot(None))
                       .order_by(recipients.c.envelope_id, recipients.c.routing_order, recipients.c.created, recipients.c.id)).mappings():
        people.setdefault(r['envelope_id'], []).append(dict(r))
    filled: dict[str, dict] = {}
    for f in c.execute(select(fields.c.envelope_id, fields.c.document_id, fields.c.recipient_id, fields.c.kind, fields.c.label, fields.c.value)
                       .where(fields.c.envelope_id.in_(ids), fields.c.kind.in_(('title', 'dropdown')), fields.c.value.isnot(None))
                       .order_by(fields.c.envelope_id, fields.c.page, fields.c.y, fields.c.x, fields.c.id)).mappings():
        e = filled.setdefault(f['envelope_id'], {'titles': {}, 'options': {}})
        if f['kind'] == 'title':
            e['titles'].setdefault(f['recipient_id'], str(f['value']).strip())
        elif str(f['label']).startswith('Fee option for '):
            m = re.fullmatch(r'\s*Option\s+([A-E])\s*', str(f['value']))
            if m:
                e['options'].setdefault((f['document_id'], f['label']), m.group(1))
                e['options'].setdefault((None, f['label']), m.group(1))
    out = []
    for e in rows:
        payload = (contracts.get(e['contract_id']) or {}).get('payload') if e['contract_id'] else None
        payload = payload if isinstance(payload, dict) else None
        signers, company_signer = [], {}
        marks = filled.get(e['id'], {'titles': {}, 'options': {}})
        for r in people.get(e['id'], []):
            title = marks['titles'].get(r['id'], '')
            if r['role_name'] == 'Company signer':
                company_signer = company_signer or {'name': _text(r['name'], 150), 'title': _text(title, 150)}
                continue
            if not title and not signers and payload:
                title = str(payload.get('client_title') or '')
            signers.append({'name': _text(r['name'], 150), 'title': _text(title, 150), 'email': _text(r['email'], 200), 'signed_at': iso(r['signed_at'])})
        if payload and isinstance(payload.get('company_signatory'), dict) and payload['company_signatory'].get('name'):
            company_signer = {'name': _text(payload['company_signatory'].get('name'), 150), 'title': _text(payload['company_signatory'].get('title'), 150)}
        certificate = None
        if e['certificate_sha256'] and e['certificate_size']:
            certificate = {'document_ref': f"c-{e['id']}", 'role': 'certificate', 'filename': f"Certificate of Completion {e['ref']}.pdf", 'mime': 'application/pdf',
                           'sha256': e['certificate_sha256'], 'size': int(e['certificate_size']), 'pages': None}
        for d in docs.get(e['id'], []):
            agreement = str(d['agreement'] or '')
            custom_key = agreement if agreement.startswith('x_') else ''
            kind = 'custom' if custom_key else client_records.kind_of_document(d['agreement'], d['name'])
            if kind not in ('baa', 'services', 'nda', 'custom'):
                kind = 'other'
            custom_type = ((payload or {}).get('custom_types') or {}).get(custom_key) if custom_key else None
            if custom_type and custom_type.get('title'):
                title = custom_type['title']
            elif d['source'] == 'generated' and agreement in server.DOCUMENT_TITLES:
                title = server.DOCUMENT_TITLES[agreement]
            else:
                title = re.sub(r'\.pdf$', '', d['name'] or 'Agreement', flags=re.I)
            carries_services = kind == 'services' or bool(custom_type and custom_type.get('services'))
            chosen = {}
            for (doc_id, label), letter in marks['options'].items():
                if doc_id in (d['id'], None):
                    chosen.setdefault(label[len('Fee option for '):], letter)
            documents = []
            if d['final_sha256'] and d['final_size']:
                documents.append({'document_ref': f"d-{d['id']}", 'role': 'signed_copy', 'filename': re.sub(r'\.pdf$', '', d['name'] or 'Agreement', flags=re.I) + ' (completed).pdf',
                                  'mime': 'application/pdf', 'sha256': d['final_sha256'], 'size': int(d['final_size']), 'pages': _int(d['pages'])})
            if certificate:
                documents.append(dict(certificate))
            agreement_view = {
                'agreement_ref': f"env:{e['id']}:{d['id']}", 'origin': 'portal_signed', 'kind': kind, 'title': _text(title, 200),
                'reference': _text(e['contract_id'] or e['ref'], 120), 'company': company_of(e['entity']),
                'signed_on': client_records.day_of(e['completed_at'], e['entity']) or None,
                'effective_on': (client_records.effective_of(payload, kind) or None) if payload else None,
                'completed_at': iso(e['completed_at']), 'governing_state': _text((payload or {}).get('governing_state'), 60),
                'client_signers': signers, 'company_signer': dict(company_signer),
                'terms': _terms(payload) if payload and carries_services else {},
                'services': services_of(payload, chosen) if payload and carries_services else [],
                'documents': documents}
            if custom_key:
                agreement_view['custom_kind'] = custom_key[:80]
            out.append(agreement_view)
    return out


def _past_agreements(c, client_id: str) -> list[dict]:
    import client_records
    rows = [dict(r) for r in c.execute(select(client_agreements).where(client_agreements.c.client_id == client_id, client_agreements.c.test.is_(False),
                                                                       or_(client_agreements.c.signed_on.isnot(None), client_agreements.c.file_id.isnot(None)))
                                       .order_by(client_agreements.c.created, client_agreements.c.id)).mappings()]
    rows = [r for r in rows if _day(r['signed_on']) or r['file_id']]
    files = {}
    file_ids = [r['file_id'] for r in rows if r['file_id']]
    if file_ids:
        files = {f['id']: dict(f) for f in c.execute(select(client_files.c.id, client_files.c.name, client_files.c.content_type, client_files.c.sha256, client_files.c.size)
                                                     .where(client_files.c.id.in_(file_ids))).mappings()}
    out = []
    for r in rows:
        kind = r['kind'] if r['kind'] in ('baa', 'services', 'nda') else 'other'
        f = files.get(r['file_id'])
        documents = []
        if f:
            documents.append({'document_ref': f"f-{f['id']}", 'role': 'signed_copy', 'filename': _text(f['name'], 200), 'mime': _text(f['content_type'], 100) or 'application/pdf',
                              'sha256': f['sha256'], 'size': int(f['size'] or 0), 'pages': None})
        out.append({'agreement_ref': f"past:{r['id']}", 'origin': 'filed_past', 'kind': kind,
                    'title': _text(r['title'] or client_records.KINDS.get(r['kind'], 'Agreement'), 200), 'reference': _text(r['reference'], 120),
                    'company': company_of(r['entity']), 'signed_on': _day(r['signed_on']), 'effective_on': _day(r['effective_on']), 'completed_at': None,
                    'governing_state': '', 'client_signers': [{'name': _text(r['client_signer'], 150), 'title': '', 'email': '', 'signed_at': None}] if (r['client_signer'] or '').strip() else [],
                    'company_signer': {'name': _text(r['company_signer'], 150), 'title': ''} if (r['company_signer'] or '').strip() else {},
                    'terms': {}, 'services': [], 'documents': documents})
    return out


def build_snapshot(c, client_id: str, now: dt.datetime | None = None) -> dict | None:
    """The client's complete current picture, or None when it is not (or no longer) published."""
    client = c.execute(select(db.clients.c.id, db.clients.c.client_number, db.clients.c.name, db.clients.c.address, db.clients.c.profile, db.clients.c.test,
                              db.clients.c.deleted_at, db.clients.c.purged_at).where(db.clients.c.id == client_id)).mappings().first()
    if not client or client['test']:
        return None
    number = str(client['client_number'] or '')
    if not re.fullmatch(r'CL-[0-9]{4,}', number):
        log.warning('client %s has a signed agreement but no client number; it is not published', client_id)
        return None
    agreements = _portal_agreements(c, client_id) + _past_agreements(c, client_id)
    if not agreements:
        return None
    # oldest first; agreements signed together keep the order of their envelope's documents (a stable sort)
    agreements.sort(key=lambda a: (a['signed_on'] or '0000-00-00', a['completed_at'] or ''))
    purged = client['purged_at'] is not None
    profile = client['profile'] if isinstance(client['profile'], dict) else {}
    contacts = []
    if not purged:
        for r in c.execute(select(client_contacts.c.id, client_contacts.c.name, client_contacts.c.title, client_contacts.c.email, client_contacts.c.phone, client_contacts.c.role)
                           .where(client_contacts.c.client_id == client_id).order_by(client_contacts.c.created, client_contacts.c.id)).mappings():
            contacts.append({'contact_ref': r['id'], 'name': _text(r['name'], 150), 'title': _text(r['title'], 150), 'email': _text(r['email'], 200),
                             'phone': _text(r['phone'], 60), 'role': r['role'] if r['role'] in ('signatory', 'billing', 'compliance', 'other') else 'other'})
    snapshot = {
        'contract': CONTRACT, 'client_ref': client['id'], 'client_number': number,
        'legal_name': f'{number} (details removed)' if purged else (_text(client['name'], 200) or number),
        'address': '' if purged else _text(client['address'], 1000),
        'phone': '' if purged else _text(profile.get('phone'), 60),
        'email': '' if purged else _text(profile.get('email'), 200),
        'website': '' if purged else _text(profile.get('website'), 300),
        'state': 'purged' if purged else 'trashed' if client['deleted_at'] is not None else 'active',
        'is_test': False, 'contacts': contacts, 'agreements': agreements}
    snapshot['snapshot_hash'] = snapshot_hash(snapshot)
    snapshot['generated_at'] = iso(now or utcnow())
    return snapshot


# ------------------------------------------------------------------ the publisher
def _lock(c) -> None:
    if c.dialect.name == 'postgresql':
        c.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': LOCK_KEY})
    else:
        c.exec_driver_sql('BEGIN IMMEDIATE')


def head_seq(c) -> int:
    return int(max(c.execute(select(func.coalesce(func.max(feed.c.seq), 0))).scalar() or 0,
                   c.execute(select(func.coalesce(func.max(published_state.c.last_seq), 0))).scalar() or 0))


def publish_client(client_id: str) -> int:
    """Rebuild one client's picture and publish it when it changed. One writer at a time (lock); returns 1 when published."""
    with db.engine.begin() as c:
        _lock(c)
        now = utcnow()
        snap = build_snapshot(c, client_id, now)
        current = c.execute(select(published_state).where(published_state.c.client_id == client_id)).mappings().first()
        if snap is not None and current and current['published'] and current['snapshot_hash'] == snap['snapshot_hash']:
            c.execute(published_state.update().where(published_state.c.client_id == client_id).values(checked_at=now))
            return 0
        if snap is None and not (current and current['published']):
            return 0
        seq = head_seq(c) + 1
        if snap is not None:
            c.execute(feed.insert().values(seq=seq, client_id=client_id, kind='client.snapshot', snapshot=snap, snapshot_hash=snap['snapshot_hash'], published_at=now))
            values = dict(snapshot_hash=snap['snapshot_hash'], last_seq=seq, published=True, checked_at=now)
        else:
            c.execute(feed.insert().values(seq=seq, client_id=client_id, kind='client.withdrawn', snapshot=None, snapshot_hash=None, published_at=now))
            values = dict(snapshot_hash=current['snapshot_hash'], last_seq=seq, published=False, checked_at=now)
        if current:
            c.execute(published_state.update().where(published_state.c.client_id == client_id).values(**values))
        else:
            c.execute(published_state.insert().values(client_id=client_id, **values))
        return 1


def changed_since(c, since: float) -> set[str]:
    """Clients whose rows changed after `since` (epoch seconds), read from the tables themselves."""
    ids: set[str] = set()
    ids.update(c.execute(select(db.clients.c.id).where(db.clients.c.updated > since)).scalars())
    ids.update(c.execute(select(envelopes.c.client_id).where(envelopes.c.updated > since, envelopes.c.client_id.isnot(None)).distinct()).scalars())
    ids.update(c.execute(select(client_agreements.c.client_id).where(client_agreements.c.updated > since).distinct()).scalars())
    ids.update(c.execute(select(client_contacts.c.client_id).where(client_contacts.c.updated > since).distinct()).scalars())
    quiet = and_(*[db.audit.c.action.notlike(p) for p in QUIET_ACTIONS])
    ids.update(c.execute(select(db.audit.c.client_id).where(db.audit.c.created > since, db.audit.c.client_id.isnot(None), quiet).distinct()).scalars())
    return {i for i in ids if i}


def every_candidate(c) -> set[str]:
    """Every client that has or had a signed agreement (and every client ever published)."""
    ids: set[str] = set(c.execute(select(envelopes.c.client_id).where(envelopes.c.status == 'completed', envelopes.c.client_id.isnot(None)).distinct()).scalars())
    ids.update(c.execute(select(client_agreements.c.client_id).distinct()).scalars())
    ids.update(c.execute(select(published_state.c.client_id)).scalars())
    return {i for i in ids if i}


def enabled(c) -> bool:
    """The feed runs once a token has ever been created (a revoked one included, so OCS can be reconnected)."""
    return bool(c.execute(select(func.count()).select_from(tokens)).scalar())


_INDEXES_READY = {'for': None}


def ensure_indexes() -> None:
    """PostgreSQL: build the indexes the publisher reads with, CONCURRENTLY and outside any transaction, so the tables
    stay writable while they are built. An index left invalid by an interrupted build is dropped and built again."""
    engine = db.engine
    if engine.dialect.name != 'postgresql' or _INDEXES_READY['for'] is engine:
        return
    with engine.connect() as c:
        c = c.execution_options(isolation_level='AUTOCOMMIT')
        for index in SOURCE_INDEXES:
            name, table = index.name, index.table.name
            valid = c.execute(text('SELECT i.indisvalid FROM pg_class x JOIN pg_index i ON i.indexrelid = x.oid WHERE x.relname = :n'), {'n': name}).scalar()
            if valid is False:
                c.exec_driver_sql(f'DROP INDEX CONCURRENTLY IF EXISTS {name}')
            if valid is not True:
                c.exec_driver_sql(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} (updated)')
    _INDEXES_READY['for'] = engine


def retention(c, now: dt.datetime) -> int:
    newest = select(func.max(feed.c.seq)).group_by(feed.c.client_id)
    gone = c.execute(feed.delete().where(feed.c.kind == 'client.snapshot', feed.c.published_at < now - dt.timedelta(seconds=KEEP_SNAPSHOTS),
                                         feed.c.seq.notin_(newest))).rowcount
    gone += c.execute(feed.delete().where(feed.c.kind == 'client.withdrawn', feed.c.published_at < now - dt.timedelta(seconds=KEEP_WITHDRAWN))).rowcount
    gone += c.execute(feed.delete().where(feed.c.kind == 'client.withdrawn', feed.c.published_at < now - dt.timedelta(seconds=KEEP_SNAPSHOTS),
                                         feed.c.seq.notin_(newest))).rowcount
    return gone


def publish_once(force_sweep: bool = False) -> int:
    """One publisher pass (called by the worker's sweep lane every 15 seconds). Returns the number of changes published."""
    with db.engine.connect() as c:
        if not enabled(c):
            return 0
        state = dict(_setting(c, SETTING_STATE, {}) or {})
        watermark = float(_setting(c, SETTING_WATERMARK, 0) or 0)
    ensure_indexes()
    started = time.time()
    sweep = force_sweep or not watermark or bool(state.get('sweep_requested')) or started - float(state.get('last_sweep') or 0) >= SWEEP_EVERY
    with db.engine.connect() as c:
        ids = every_candidate(c) if sweep else changed_since(c, watermark - OVERLAP)
    ids |= set(state.get('retry') or [])
    published, failed = 0, []
    for client_id in sorted(ids):
        try:
            published += publish_client(client_id)
        except Exception:  # noqa: BLE001  (one client that cannot be built never stops the others; it is tried again next time)
            log.exception('integration feed: client %s could not be published', client_id)
            failed.append(client_id)
    with db.engine.begin() as c:
        state = dict(_setting(c, SETTING_STATE, {}) or {})
        state.update(last_run=started, retry=failed[:500])
        if published:
            state['last_published'] = time.time()
        if sweep:
            state.update(last_sweep=started, sweep_requested=False)
            removed = retention(c, utcnow())
            if removed:
                log.info('integration feed: %s old changes removed (each client keeps its newest)', removed)
        _put(c, SETTING_STATE, state)
        _put(c, SETTING_WATERMARK, started)
    return published


# ------------------------------------------------------------------ what the feed serves
def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({'error': code, 'message': message}, status)


def published_pairs(c) -> list[tuple[str, str]]:
    return [(r[0], r[1]) for r in c.execute(select(published_state.c.client_id, published_state.c.snapshot_hash).where(published_state.c.published.is_(True)))]


def meta(c) -> dict:
    state = _setting(c, SETTING_STATE, {}) or {}
    pairs = published_pairs(c)
    return {'contract': CONTRACT, 'contract_revision': CONTRACT_REVISION, 'agreements_release': RELEASE, 'instance_id': instance_id(c),
            'head_seq': head_seq(c), 'published_clients': len(pairs), 'state_digest': state_digest(pairs),
            'last_sweep_at': iso(state.get('last_sweep')) if state.get('last_sweep') else None, 'generated_at': iso(utcnow())}


def change_view(r) -> dict:
    out = {'seq': int(r['seq']), 'kind': r['kind'], 'client_ref': r['client_id'], 'published_at': iso(r['published_at'])}
    if r['kind'] == 'client.snapshot':
        out['snapshot'] = r['snapshot']
    return out


def current_snapshot(c, client_id: str) -> dict | None:
    state = c.execute(select(published_state.c.published, published_state.c.last_seq).where(published_state.c.client_id == client_id)).first()
    if not state or not state[0]:
        return None
    row = c.execute(select(feed.c.snapshot).where(feed.c.seq == state[1], feed.c.kind == 'client.snapshot')).first()
    return row[0] if row else None


def catalog(c) -> dict:
    import catalog_admin
    import doc_types
    import server
    from entities import ENTITIES
    custom = set(catalog_admin.custom_models())
    model_id = lambda m: f'custom:{m}' if m in custom else m   # noqa: E731
    companies = [{'key': str(e['key']), 'legal_name': name, 'short_name': str(e.get('short') or name), 'governing_state': str(e.get('governing_state') or ''),
                  'active': e.get('active', True) is not False} for name, e in ENTITIES.items()]
    services, seen = [], set()
    for s in catalog_admin.catalog(include_inactive=True):
        seen.add(s['id'])
        services.append({'service_id': s['id'], 'name': s['name'], 'category': s.get('category') if s.get('category') in ('recurring', 'on_demand') else '',
                         'pricing_models': [model_id(m['id']) for m in s.get('models') or [] if isinstance(m, dict) and m.get('id')], 'active': bool(s.get('active', True))})
    for e in ENTITIES.values():   # services a company added in the app offers on its own agreements
        for s in e.get('services') or []:
            if isinstance(s, dict) and s.get('id') and s['id'] not in seen:
                seen.add(s['id'])
                services.append({'service_id': s['id'], 'name': str(s.get('name') or s['id']), 'category': s.get('category') if s.get('category') in ('recurring', 'on_demand') else '',
                                 'pricing_models': [model_id(m['id'] if isinstance(m, dict) else m) for m in s.get('models') or []], 'active': True})
    types = [{'kind': k, 'title': t} for k, t in server.DOCUMENT_TITLES.items()]
    types += [{'kind': 'custom', 'custom_kind': k, 'title': str(t.get('title') or k)} for k, t in sorted(doc_types.read(c)['custom'].items())]
    return {'contract': CONTRACT, 'companies': companies, 'services': services, 'agreement_types': types}


def document(c, ref: str) -> tuple[bytes, dict] | None:
    """The bytes of a document named in a currently published snapshot, and its entry in that snapshot."""
    m = re.fullmatch(r'([dcf])-([a-f0-9]{32})', ref or '')
    if not m:
        return None
    kind, key = m.groups()
    if kind == 'd':
        row = c.execute(select(esign_documents.c.final_pdf, envelopes.c.client_id).join(envelopes, envelopes.c.id == esign_documents.c.envelope_id)
                        .where(esign_documents.c.id == key)).first()
    elif kind == 'c':
        row = c.execute(select(envelopes.c.certificate, envelopes.c.client_id).where(envelopes.c.id == key)).first()
    else:
        row = c.execute(select(client_files.c.body, client_files.c.client_id).where(client_files.c.id == key)).first()
    if not row or not row[0] or not row[1]:
        return None
    snap = current_snapshot(c, row[1])
    entry = next((d for a in (snap or {}).get('agreements', []) for d in a.get('documents', []) if d.get('document_ref') == ref), None)
    return (bytes(row[0]), entry) if entry else None


# ------------------------------------------------------------------ tokens
def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else '')[:64]


def authorize(request: Request) -> JSONResponse | None:
    """None when the request carries a valid, unrevoked token; otherwise the error to answer."""
    ip = _client_ip(request)
    key = 'integration:' + ip
    header = request.headers.get('authorization', '')
    given = header[7:].strip() if header[:7].lower() == 'bearer ' else ''
    with db.engine.begin() as c:
        if sec.rate_blocked(c, key, FAILED_LIMIT):
            return _error(429, 'too_many_requests', 'Too many requests without a valid token from this address. Wait 15 minutes and try again.')
        match = None
        if given:
            wanted = token_digest(given)
            for row in c.execute(select(tokens.c.id, tokens.c.token_hash, tokens.c.revoked, tokens.c.last_used)).mappings():
                if hmac.compare_digest(row['token_hash'], wanted):
                    match = row
        if not match or match['revoked'] is not None:
            sec.rate_limit(c, key, FAILED_LIMIT)
            return _error(401, 'unauthorized', 'A valid token is needed. Create one in TFS Agreements under Admin › Integrations.')
        now = utcnow()
        last = match['last_used']
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=dt.UTC)
        if last is None or (now - last).total_seconds() > 60:
            c.execute(tokens.update().where(tokens.c.id == match['id']).values(last_used=now, last_ip=ip))
    return None


def _as_int(value: str | None, default: int) -> int | None:
    if value is None or value == '':
        return default
    return int(value) if re.fullmatch(r'[0-9]{1,15}', value) else None


# ------------------------------------------------------------------ Admin › Integrations
def admin_view(c) -> dict:
    state = _setting(c, SETTING_STATE, {}) or {}
    watermark = float(_setting(c, SETTING_WATERMARK, 0) or 0)
    names = dict(c.execute(select(db.users.c.id, db.users.c.name)).all())
    rows = c.execute(select(tokens).order_by(tokens.c.created.desc())).mappings().all()
    newest = c.execute(select(feed.c.published_at).order_by(feed.c.seq.desc()).limit(1)).scalar()
    return {'enabled': bool(rows), 'instance_id': instance_id(c), 'feed_path': PREFIX, 'contract_revision': CONTRACT_REVISION, 'head_seq': head_seq(c),
            'published_clients': len(published_pairs(c)), 'last_published_at': iso(newest), 'last_run_at': iso(state.get('last_run')) if state.get('last_run') else None,
            'last_sweep_at': iso(state.get('last_sweep')) if state.get('last_sweep') else None, 'sweep_requested': bool(state.get('sweep_requested')),
            'lag_seconds': round(time.time() - watermark) if rows and watermark else None, 'retrying': len(state.get('retry') or []),
            'tokens': [{'id': r['id'], 'name': r['name'], 'created': iso(r['created']), 'created_by': names.get(r['created_by'], ''), 'last_used': iso(r['last_used']),
                        'last_ip': r['last_ip'] or '', 'revoked': iso(r['revoked'])} for r in rows]}


def health_items(c, now: float) -> list[dict]:
    """For the System check: nothing until a token exists (an installation that is not connected looks as before)."""
    import health
    if not enabled(c):
        return []
    state = _setting(c, SETTING_STATE, {}) or {}
    watermark = float(_setting(c, SETTING_WATERMARK, 0) or 0)
    newest = c.execute(select(feed.c.published_at).order_by(feed.c.seq.desc()).limit(1)).scalar()
    last = f'last published {health.clock(newest.timestamp() if newest.tzinfo else newest.replace(tzinfo=dt.UTC).timestamp())}' if newest else 'nothing published yet'
    if not watermark:
        return [health.item('integration', 'Integration feed (OCS)', 'warn', 'Waiting for its first run (within a minute of a token being created).', '#/admin/integrations')]
    lag = now - watermark
    detail = f'{last}; lag {health.ago(lag)}.'
    if state.get('retry'):
        detail += f" {len(state['retry'])} client(s) could not be published and are retried every run; the server log has the reason."
    state_name = 'ok' if lag < 300 and not state.get('retry') else 'warn' if lag < 3600 else 'bad'
    return [health.item('integration', 'Integration feed (OCS)', state_name, detail[0].upper() + detail[1:], '#/admin/integrations')]


def install(app) -> None:
    @app.get(PREFIX + '/meta')
    def api_meta(request: Request):
        refused = authorize(request)
        if refused:
            return refused
        with db.engine.connect() as c:
            return meta(c)

    @app.get(PREFIX + '/changes')
    def api_changes(request: Request):
        refused = authorize(request)
        if refused:
            return refused
        after = _as_int(request.query_params.get('after'), 0)
        limit = _as_int(request.query_params.get('limit'), 100)
        if after is None:
            return _error(400, 'bad_request', '"after" must be a whole number of 0 or more.')
        if limit is None or not 1 <= limit <= MAX_LIMIT:
            return _error(400, 'bad_request', f'"limit" must be a whole number from 1 to {MAX_LIMIT}.')
        with db.engine.connect() as c:
            head = head_seq(c)
            rows = c.execute(select(feed).where(feed.c.seq > after).order_by(feed.c.seq).limit(limit + 1)).mappings().all()
            return {'contract': CONTRACT, 'instance_id': instance_id(c), 'head_seq': head, 'has_more': len(rows) > limit,
                    'changes': [change_view(r) for r in rows[:limit]]}

    @app.get(PREFIX + '/clients/{client_ref}')
    def api_client(client_ref: str, request: Request):
        refused = authorize(request)
        if refused:
            return refused
        with db.engine.connect() as c:
            snap = current_snapshot(c, client_ref) if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', client_ref) else None
        return snap if snap else _error(404, 'not_found', 'This client is not published.')

    @app.get(PREFIX + '/documents/{document_ref}')
    def api_document(document_ref: str, request: Request):
        refused = authorize(request)
        if refused:
            return refused
        with db.engine.connect() as c:
            found = document(c, document_ref)
        if not found:
            return _error(404, 'not_found', 'This document is not part of a published agreement.')
        body, entry = found
        name = re.sub(r'["\\\r\n]', '', entry.get('filename') or 'document.pdf')
        return Response(body, media_type=entry.get('mime') or 'application/pdf',
                        headers={'Content-Length': str(len(body)), 'X-Content-SHA256': hashlib.sha256(body).hexdigest(),
                                 'Content-Disposition': f'attachment; filename="{name}"'})

    @app.get(PREFIX + '/catalog')
    def api_catalog(request: Request):
        refused = authorize(request)
        if refused:
            return refused
        with db.engine.connect() as c:
            return catalog(c)

    @app.get(PREFIX + '/{rest:path}')
    def api_unknown(rest: str, request: Request):
        refused = authorize(request)
        return refused or _error(404, 'not_found', 'There is no such address in the feed.')

    # -------------------------------------------------------------- Admin › Integrations (Super Admin)
    def super_admin(request, c):
        a = sec.get_actor(request, c)
        if a['role'] != 'super_admin':
            raise HTTPException(403, 'Only the Super Admin can manage integrations.')
        return a

    @app.get('/api/admin/integrations')
    def api_admin(request: Request):
        with db.engine.connect() as c:
            super_admin(request, c)
            return admin_view(c)

    @app.post('/api/admin/integrations/tokens')
    def api_token_create(request: Request, payload: dict = Body(...)):
        name = re.sub(r'\s+', ' ', str(payload.get('name') or '')).strip()
        if not name or len(name) > 100:
            raise HTTPException(400, 'Give the token a name of up to 100 characters, for example "OCS".')
        token = 'tfsa_' + secrets.token_urlsafe(32)
        with db.engine.begin() as c:
            a = super_admin(request, c)
            row = dict(id=db.uid(), name=name, token_hash=token_digest(token), created=utcnow(), created_by=a['id'])
            c.execute(tokens.insert().values(**row))
            db.log(c, a, 'integration.token_created', detail={'token': row['id'], 'name': name})
            view = admin_view(c)
        return {'token': token, 'id': row['id'], 'view': view}

    @app.post('/api/admin/integrations/tokens/{token_id}/revoke')
    def api_token_revoke(token_id: str, request: Request):
        with db.engine.begin() as c:
            a = super_admin(request, c)
            row = c.execute(select(tokens.c.id, tokens.c.name, tokens.c.revoked).where(tokens.c.id == token_id)).first()
            if not row:
                raise HTTPException(404, 'Token not found.')
            if row[2] is None:
                c.execute(tokens.update().where(tokens.c.id == token_id).values(revoked=utcnow()))
                db.log(c, a, 'integration.token_revoked', detail={'token': token_id, 'name': row[1]})
            return admin_view(c)

    @app.post('/api/admin/integrations/republish')
    def api_republish(request: Request):
        with db.engine.begin() as c:
            a = super_admin(request, c)
            if not enabled(c):
                raise HTTPException(400, 'Create a token first: the feed starts with the first token.')
            state = dict(_setting(c, SETTING_STATE, {}) or {})
            state['sweep_requested'] = True
            _put(c, SETTING_STATE, state)
            db.log(c, a, 'integration.republish_requested')
            return admin_view(c)

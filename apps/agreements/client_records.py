"""The client database: the clients register, each client's agreements from every system, past signed agreements and the
Signed BAA register.

Agreements come from three places:
  * this portal: generated agreements (`contracts` and their documents) and what was sent for signature (envelopes; a
    completed envelope is a signed copy with its Certificate of Completion);
  * earlier systems: `client_agreements`, agreements signed before this portal (returned by email, DocuSign, Jotform), each
    with its signed copy kept as a client file.

A client is "covered" when every company it works with (through generated agreements, envelopes or past agreements) has a
signed Business Associate Agreement on file. Test clients, test agreements and test envelopes are practice runs and stay out
of the register unless asked for. Access follows the rest of the portal: a User sees their own clients, an Admin their team's,
the Super Admin every client; company permissions always apply.

The register is computed from a handful of grouped queries over the visible clients (no query per client), then filtered,
counted and paged in memory, which stays fast for a few thousand clients.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import re
import tempfile
import time
import zipfile
import zoneinfo

import client_numbers
import client_trash
import clients as client_profiles
import database as db
import esign
import pdf_facts
import security as sec
from entities import ENTITIES
from esign_db import client_agreements, client_files, envelopes, fields, recipients
from esign_db import documents as esign_documents
from fastapi import Body, HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete, select, update
from starlette.background import BackgroundTask

PAGE = 25
KINDS = {'baa': 'Business Associate Agreement', 'services': 'Services Agreement', 'nda': 'Mutual NDA', 'other': 'Other agreement'}
TAG_KINDS = ('baa', 'services', 'nda')
SOURCES = {'portal': 'Portal', 'docusign': 'DocuSign', 'manual': 'Manual (email)', 'jotform': 'Jotform', 'other': 'Other'}
PAST_SOURCES = ('docusign', 'manual', 'jotform', 'other')
DONE = 'completed'
OUT = ('sent', 'completing')


# ------------------------------------------------------------------ small helpers
def kind_of_document(agreement, name) -> str:
    """The agreement a document is: the generated kind, or what its name says (uploaded documents)."""
    if agreement in TAG_KINDS:
        return agreement
    n = ' ' + pdf_facts.norm(name or '') + ' '
    if ' business associate ' in n or ' baa ' in n:
        return 'baa'
    if ' services agreement ' in n or ' service agreement ' in n:
        return 'services'
    if ' nondisclosure ' in n or ' non disclosure ' in n or ' nda ' in n or ' confidentiality agreement ' in n:
        return 'nda'
    return 'other'


_TZ: dict[str, dt.tzinfo] = {}


def day_of(ts: float | None, entity: str) -> str:
    """The date (YYYY-MM-DD) of a moment, in the company's time zone (the date printed on its signed agreements)."""
    if not ts:
        return ''
    if entity not in _TZ:
        try:
            _TZ[entity] = zoneinfo.ZoneInfo(ENTITIES[entity]['timezone'])
        except Exception:
            _TZ[entity] = dt.UTC
    return dt.datetime.fromtimestamp(ts, _TZ[entity]).date().isoformat()


def ts_of(day: str | None) -> float:
    try:
        return dt.datetime.fromisoformat(day).replace(hour=12, tzinfo=dt.UTC).timestamp() if day else 0.0
    except ValueError:
        return 0.0


def company_view(name: str) -> dict:
    e = ENTITIES[name] if name in ENTITIES else None
    if not e:
        return {'name': name, 'short': name, 'color': '#94a1b4', 'accent': '#cfd7e3'}
    return {'name': name, 'short': e.get('short') or name, 'color': e.get('color') or '#1f55d6', 'accent': e.get('accent') or e.get('color') or '#1f55d6'}


def visible_companies(actor: dict) -> list[str]:
    names = list(ENTITIES)
    return names if actor['role'] == 'super_admin' else [n for n in names if n in actor['companies']]


def place_of(address: str) -> str:
    """'City, ST' from a US address ('…, Austin, TX 78704')."""
    parts = [p.strip() for p in (address or '').split(',') if p.strip()]
    for i in range(len(parts) - 1, 0, -1):
        m = re.fullmatch(r'([A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?', parts[i])
        if m:
            return f'{parts[i - 1]}, {m.group(1)}'
    return ''


def clean_day(value, label: str) -> str | None:
    value = esign.clean(value, label, 10)
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f'{label} must be a valid date.') from None


def _chunks(ids: list, size: int = 800):
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


# ------------------------------------------------------------------ facts: what is on file for each visible client
def visible_clients(actor: dict, test: str = 'real'):
    cond = [sec.scope(actor, db.clients.c.owner_id), client_trash.live()]
    if test == 'real':
        cond.append(db.clients.c.test.is_(False))
    elif test == 'test':
        cond.append(db.clients.c.test.is_(True))
    return cond


def gather(c, actor: dict, test: str = 'real', client_ids: list[str] | None = None) -> tuple[list[dict], dict]:
    """The visible clients and, for each, what agreements it has: {client_id: facts}."""
    cond = visible_clients(actor, test)
    if client_ids is not None:
        cond.append(db.clients.c.id.in_(client_ids or ['']))
    rows = [dict(r) for r in c.execute(select(db.clients.c.id, db.clients.c.client_number, db.clients.c.name, db.clients.c.address, db.clients.c.profile,
                                              db.clients.c.updated, db.clients.c.created, db.clients.c.test, db.clients.c.owner_id,
                                              db.clients.c.draft['entity'].as_string().label('draft_entity'),
                                              db.clients.c.draft['client_email'].as_string().label('draft_email')).where(*cond)).mappings()]
    include_test = test != 'real'
    facts = {r['id']: {'companies': [], 'status': {}, 'baa': {}, 'signed': {}, 'sent': {}, 'past_sources': [], 'first_past': None, 'last': 0.0,
                       'last_source': '', 'past': 0, 'portal': False}
             for r in rows}
    ids = list(facts)

    def company(f, entity):
        if entity not in f['companies']:
            f['companies'].append(entity)

    def mark(f, entity, kind, state):
        order = {'signed': 3, 'out': 2, 'generated': 1}
        key = (entity, kind)
        if order[state] > order.get(f['status'].get(key, ''), 0):
            f['status'][key] = state

    def activity(f, at, source):
        if at and at > f['last']:
            f['last'], f['last_source'] = at, source

    def signed(f, entity, kind, day, source, at=None, ref=None):
        # the earliest signed agreement of this kind with this company (an unknown date sorts after known ones)
        key = (entity, kind)
        current = f['signed'].get(key)
        if not current or (day and (not current['date'] or day < current['date'])):
            f['signed'][key] = {'date': day or '', 'source': source, 'at': at, 'ref': ref}
        if kind == 'baa':
            f['baa'][entity] = f['signed'][key]

    def sent(f, entity, kind, day):
        if day and day > f['sent'].get((entity, kind), ''):
            f['sent'][(entity, kind)] = day

    for part in _chunks(ids):
        q = select(db.contracts.c.client_id, db.contracts.c.entity, db.contracts.c.created, db.documents.c.agreement).join(
            db.documents, db.documents.c.contract_id == db.contracts.c.id).where(
            db.contracts.c.client_id.in_(part), sec.scope(actor, db.contracts.c.owner_id), sec.company_scope(actor, db.contracts.c.entity))
        if not include_test:
            q = q.where(db.contracts.c.test.is_(False))
        for r in c.execute(q).mappings():
            f = facts[r['client_id']]
            f['portal'] = True
            company(f, r['entity'])
            mark(f, r['entity'], kind_of_document(r['agreement'], ''), 'generated')
            activity(f, r['created'], 'portal')
        q = select(envelopes.c.id, envelopes.c.client_id, envelopes.c.entity, envelopes.c.status, envelopes.c.completed_at, envelopes.c.updated,
                   envelopes.c.sent_at, esign_documents.c.agreement, esign_documents.c.name).join(esign_documents, esign_documents.c.envelope_id == envelopes.c.id).where(
            envelopes.c.client_id.in_(part), esign.visible(actor), envelopes.c.status.notin_(('template', 'draft')), esign_documents.c.source != 'attachment')
        if not include_test:
            q = q.where(envelopes.c.test.is_(False))
        for r in c.execute(q).mappings():
            f = facts[r['client_id']]
            kind = kind_of_document(r['agreement'], r['name'])
            f['portal'] = True
            company(f, r['entity'])
            activity(f, r['updated'], 'portal')
            if r['status'] == DONE:
                mark(f, r['entity'], kind, 'signed')
                signed(f, r['entity'], kind, day_of(r['completed_at'], r['entity']), 'portal', r['completed_at'], r['id'])
            elif r['status'] in OUT:
                mark(f, r['entity'], kind, 'out')
                sent(f, r['entity'], kind, day_of(r['sent_at'] or r['updated'], r['entity']))
        q = select(client_agreements.c.id, client_agreements.c.client_id, client_agreements.c.entity, client_agreements.c.kind, client_agreements.c.source,
                   client_agreements.c.signed_on, client_agreements.c.created).where(client_agreements.c.client_id.in_(part),
                                                                                     sec.company_scope(actor, client_agreements.c.entity))
        if not include_test:
            q = q.where(client_agreements.c.test.is_(False))
        for r in c.execute(q.order_by(client_agreements.c.signed_on, client_agreements.c.created)).mappings():
            f = facts[r['client_id']]
            company(f, r['entity'])
            mark(f, r['entity'], r['kind'], 'signed')
            activity(f, ts_of(r['signed_on']) or r['created'], r['source'])
            f['past'] += 1
            if r['source'] not in f['past_sources']:
                f['past_sources'].append(r['source'])
            if f['first_past'] is None:
                f['first_past'] = r['source']
            signed(f, r['entity'], r['kind'], r['signed_on'], r['source'], ref=r['id'])
    for r in rows:
        f = facts[r['id']]
        if not f['last']:
            f['last'] = r['updated'] or r['created'] or 0
        f['covered'] = bool(f['companies']) and all(e in f['baa'] for e in f['companies'])
    return rows, facts


def first_baa(f: dict, company: str = '') -> dict | None:
    items = [dict(v, entity=k) for k, v in f['baa'].items() if not company or k == company]
    if not items:
        return None
    return min(items, key=lambda x: (not x['date'], x['date']))


def doc_states(f: dict, company: str = '') -> dict:
    out = {}
    for kind in TAG_KINDS:
        states = [s for (e, k), s in f['status'].items() if k == kind and (not company or e == company)]
        out[kind] = 'signed' if 'signed' in states else 'out' if 'out' in states else 'none'
    return out


# ------------------------------------------------------------------ the clients register
def matches(r: dict, q: str) -> bool:
    if not q:
        return True
    term = q.strip().casefold()
    exact = client_numbers.search_terms(q)
    if exact and r['client_number'] in exact:
        return True
    p = r['profile'] or {}
    hay = ' '.join(str(x or '') for x in (r['client_number'], r['name'], r['address'], p.get('npi'), p.get('email'), p.get('phone'), r.get('draft_email'))).casefold()
    return term in hay


def agreement_view(f: dict, kind: str, company: str = '') -> dict:
    """One agreement type for one client: signed (with the date it was signed), out for signature (with the date sent) or not signed."""
    state = doc_states(f, company)[kind]
    recs = [v for (e, k), v in f['signed'].items() if k == kind and (not company or e == company)]
    first = min(recs, key=lambda x: (not x['date'], x['date'])) if recs else None
    sent_on = max([d for (e, k), d in f['sent'].items() if k == kind and (not company or e == company)] or [''])
    return {'state': state, 'date': first['date'] if first else '', 'source': first['source'] if first else '', 'sent': sent_on}


SHOW = ('all', 'both', 'waiting', 'no_baa', 'no_services')


def register(c, actor: dict, q: str = '', company: str = '', source: str = '', baa: str = 'all', test: str = 'real', page: int = 0, show: str = 'all') -> dict:
    rows, facts = gather(c, actor, test)
    total = len(rows)
    earlier = [r for r in rows if facts[r['id']]['past']]
    by_source = {s: 0 for s in PAST_SOURCES}
    for r in earlier:
        by_source[facts[r['id']]['first_past']] = by_source.get(facts[r['id']]['first_past'], 0) + 1
    covered = sum(1 for r in rows if facts[r['id']]['covered'])
    services = sum(1 for r in rows if doc_states(facts[r['id']])['services'] == 'signed')
    waiting = [r for r in rows if 'out' in doc_states(facts[r['id']]).values()]
    kpis = {'clients': total, 'earlier': len(earlier), 'portal_only': total - len(earlier), 'covered': covered, 'missing': total - covered,
            'coverage': round(covered * 100 / total) if total else 0, 'earlier_by_source': by_source,
            'services_signed': services, 'services_coverage': round(services * 100 / total) if total else 0, 'waiting': len(waiting),
            'waiting_names': [r['name'] for r in waiting[:3]]}

    def signed(r):
        f = facts[r['id']]
        return company in f['baa'] if company else f['covered']

    def services_signed(r):
        return doc_states(facts[r['id']], company)['services'] == 'signed'

    def is_waiting(r):
        return 'out' in doc_states(facts[r['id']], company).values()

    tests = {'both': lambda r: signed(r) and services_signed(r), 'waiting': is_waiting, 'no_baa': lambda r: not signed(r),
             'no_services': lambda r: not services_signed(r)}
    found = [r for r in rows if matches(r, q)
             and (not company or company in facts[r['id']]['companies'])
             and (not source or source in sources_of(facts[r['id']]))]
    counts = {'all': len(found), 'signed': sum(1 for r in found if signed(r))}
    counts['missing'] = counts['all'] - counts['signed']
    for key, fn in tests.items():
        counts[key] = sum(1 for r in found if fn(r))
    if baa == 'signed':
        found = [r for r in found if signed(r)]
    elif baa == 'missing':
        found = [r for r in found if not signed(r)]
    if show in tests:
        found = [r for r in found if tests[show](r)]
    exact = client_numbers.search_terms(q)
    found.sort(key=lambda r: (not (exact and r['client_number'] in exact), client_numbers.parse(r['client_number']) or 10 ** 9, r['name'].casefold()))
    page = max(0, min(int(page or 0), max(0, (len(found) - 1) // PAGE)))
    out = []
    for r in found[page * PAGE:(page + 1) * PAGE]:
        f = facts[r['id']]
        first = first_baa(f, company)
        missing_for = [company_view(e)['short'] for e in f['companies'] if e not in f['baa'] and (not company or e == company)]
        companies = f['companies'] or ([r['draft_entity']] if r['draft_entity'] in ENTITIES and (actor['role'] == 'super_admin' or r['draft_entity'] in actor['companies']) else [])
        p = r['profile'] or {}
        out.append({'id': r['id'], 'client_number': r['client_number'] or '', 'name': r['name'], 'place': place_of(r['address']), 'npi': p.get('npi', ''),
                    'test': bool(r['test']), 'companies': [company_view(e) | {'linked': e in f['companies']} for e in companies],
                    'docs': doc_states(f, company), 'agreements': {k: agreement_view(f, k, company) for k in TAG_KINDS},
                    'baa': first and {'date': first['date'], 'source': first['source'], 'company': company_view(first['entity'])['short']},
                    'baa_missing_for': missing_for if first else [], 'covered': signed(r),
                    'through': first['source'] if first else (f['last_source'] if f['companies'] else ''), 'last_activity': f['last']})
    return {'kpis': kpis, 'counts': counts, 'clients': out, 'total': len(found), 'page': page, 'page_size': PAGE,
            'companies': [company_view(n) for n in visible_companies(actor)], 'sources': SOURCES,
            'can_import': actor['role'] != 'user', 'can_delete': actor['role'] == 'super_admin', 'trash': client_trash.count(c, actor)}


def sources_of(f: dict) -> set[str]:
    out = set(f['past_sources'])
    if f.get('portal'):
        out.add('portal')
    return out


# ------------------------------------------------------------------ one client's agreements (the client record)
def service_names(payload: dict, entity: str) -> list[str]:
    import server
    names = {s['id']: s['name'] for s in server.CATALOG}
    try:
        names.update({s['id']: s['name'] for s in (ENTITIES[entity].get('services') or []) if isinstance(s, dict) and s.get('id')})
    except KeyError:
        pass
    out = [names.get(s.get('id'), '') for s in payload.get('services') or [] if isinstance(s, dict)]
    out += [s.get('name', '') for s in payload.get('custom_services') or [] if isinstance(s, dict)]
    return [n for n in out if n]


def effective_of(payload: dict | None, kind: str) -> str:
    payload = payload or {}
    value = (payload.get('baa_effective_date') if kind == 'baa' else '') or payload.get('effective_date') or ''
    return value if re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(value)) else ''


def title_of(kind: str, payload: dict | None, entity: str, name: str = '') -> str:
    if kind == 'other':
        return re.sub(r'\.pdf$', '', name or 'Document', flags=re.I)
    title = KINDS[kind]
    if kind == 'services' and payload:
        names = service_names(payload, entity)
        if names:
            title += ' · ' + ', '.join(names[:3]) + (f' and {len(names) - 3} more' if len(names) > 3 else '')
    return title


def signers(c, envelope_ids: list[str]) -> dict[str, dict]:
    """For each envelope: the client's signer (name, title) and the company signer."""
    out: dict[str, dict] = {}
    if not envelope_ids:
        return out
    titles = {}
    for r in c.execute(select(fields.c.recipient_id, fields.c.value).where(fields.c.envelope_id.in_(envelope_ids), fields.c.kind == 'title',
                                                                           fields.c.value.isnot(None))).all():
        titles.setdefault(r[0], r[1])
    for r in c.execute(select(recipients.c.id, recipients.c.envelope_id, recipients.c.name, recipients.c.role, recipients.c.role_name, recipients.c.routing_order)
                       .where(recipients.c.envelope_id.in_(envelope_ids), recipients.c.role.in_(esign.SIGNING_ROLES))
                       .order_by(recipients.c.routing_order)).mappings():
        e = out.setdefault(r['envelope_id'], {'client': '', 'company': ''})
        who = ', '.join(x for x in (r['name'], titles.get(r['id'], '')) if x)
        if r['role_name'] == 'Company signer':
            e['company'] = e['company'] or who
        else:
            e['client'] = e['client'] or who
    return out


def can_change(actor: dict, client: dict, row: dict) -> bool:
    return actor['role'] != 'user' or row.get('created_by') == actor['id'] or client['owner_id'] == actor['id']


def past_view(row: dict, actor: dict, client: dict, files: dict) -> dict:
    f = files.get(row['file_id']) if row['file_id'] else None
    return {'id': row['id'], 'origin': 'past', 'kind': row['kind'], 'title': row['title'] or KINDS.get(row['kind'], 'Agreement'), 'company': company_view(row['entity']),
            'source': row['source'], 'status': 'signed', 'signed_on': row['signed_on'] or '', 'effective_on': row['effective_on'] or '',
            'client_signer': row['client_signer'], 'company_signer': row['company_signer'], 'reference': row['reference'], 'notes': row['notes'],
            'file': f and {'id': f['id'], 'name': f['name'], 'size': f['size'], 'url': f"/api/clients/files/{f['id']}"},
            'test': bool(row['test']), 'editable': can_change(actor, client, row), 'created': row['created']}


def client_agreement_list(c, actor: dict, client: dict) -> dict:
    cid = client['id']
    items = []
    sent = [dict(r) for r in c.execute(select(envelopes.c.id, envelopes.c.ref, envelopes.c.status, envelopes.c.entity, envelopes.c.completed_at, envelopes.c.sent_at,
                                              envelopes.c.created, envelopes.c.updated, envelopes.c.contract_id, envelopes.c.test, envelopes.c.certificate_sha256)
                                       .where(envelopes.c.client_id == cid, esign.visible(actor), envelopes.c.status.notin_(('template', 'draft')))).mappings()]
    by_env = {e['id']: e for e in sent}
    contracts = {r['id']: dict(r) for r in c.execute(select(db.contracts.c.id, db.contracts.c.entity, db.contracts.c.created, db.contracts.c.payload, db.contracts.c.test)
                                                     .where(db.contracts.c.client_id == cid, sec.scope(actor, db.contracts.c.owner_id),
                                                            sec.company_scope(actor, db.contracts.c.entity))).mappings()}
    payloads = dict(contracts)
    missing = [e['contract_id'] for e in sent if e['contract_id'] and e['contract_id'] not in payloads]
    if missing:
        payloads.update({r['id']: dict(r) for r in c.execute(select(db.contracts.c.id, db.contracts.c.payload).where(db.contracts.c.id.in_(missing))).mappings()})
    who = signers(c, [e['id'] for e in sent if e['status'] == DONE])
    if sent:
        for d in c.execute(select(esign_documents.c.id, esign_documents.c.envelope_id, esign_documents.c.name, esign_documents.c.agreement, esign_documents.c.final_sha256)
                           .where(esign_documents.c.envelope_id.in_(list(by_env)), esign_documents.c.source != 'attachment')
                           .order_by(esign_documents.c.position)).mappings():
            e = by_env[d['envelope_id']]
            kind = kind_of_document(d['agreement'], d['name'])
            payload = (payloads.get(e['contract_id']) or {}).get('payload') if e['contract_id'] else None
            done = e['status'] == DONE
            status = 'signed' if done else 'out' if e['status'] in OUT else e['status']
            links = []
            if done and d['final_sha256']:
                links.append({'label': 'Signed PDF', 'url': f"/api/esign/documents/{d['id']}/file?final=1"})
            if done and e['certificate_sha256']:
                links.append({'label': 'Certificate', 'url': f"/api/esign/envelopes/{e['id']}/certificate"})
            items.append({'id': d['id'], 'origin': 'portal', 'kind': kind, 'title': title_of(kind, payload, e['entity'], d['name']), 'company': company_view(e['entity']),
                          'source': 'portal', 'status': status, 'signed_on': day_of(e['completed_at'], e['entity']) if done else '',
                          'effective_on': effective_of(payload, kind), 'reference': e['ref'], 'envelope_id': e['id'], 'links': links,
                          'client_signer': who.get(e['id'], {}).get('client', ''), 'company_signer': who.get(e['id'], {}).get('company', ''),
                          'test': bool(e['test']), 'created': e['sent_at'] or e['created']})
    sent_contracts = {e['contract_id'] for e in sent if e['contract_id']}
    unsent = [k for k in contracts if k not in sent_contracts]
    if unsent:
        docs: dict[tuple, list] = {}
        for d in c.execute(select(db.documents.c.id, db.documents.c.contract_id, db.documents.c.kind, db.documents.c.agreement)
                           .where(db.documents.c.contract_id.in_(unsent))).mappings():
            docs.setdefault((d['contract_id'], kind_of_document(d['agreement'], '')), []).append(d)
        for (contract_id, kind), ds in docs.items():
            k = contracts[contract_id]
            links = [{'label': 'PDF' if x['kind'] == 'pdf' else 'Word', 'url': f"/files/{x['id']}"} for x in sorted(ds, key=lambda x: x['kind'] != 'pdf')]
            items.append({'id': f'{contract_id}-{kind}', 'origin': 'generated', 'kind': kind, 'title': title_of(kind, k['payload'], k['entity']),
                          'company': company_view(k['entity']), 'source': 'portal', 'status': 'generated', 'signed_on': '',
                          'effective_on': effective_of(k['payload'], kind), 'reference': contract_id, 'contract_id': contract_id, 'links': links,
                          'test': bool(k['test']), 'created': k['created']})
    past = [dict(r) for r in c.execute(select(client_agreements).where(client_agreements.c.client_id == cid, sec.company_scope(actor, client_agreements.c.entity))).mappings()]
    files = {}
    file_ids = [r['file_id'] for r in past if r['file_id']]
    if file_ids:
        files = {r['id']: dict(r) for r in c.execute(select(client_files.c.id, client_files.c.name, client_files.c.size).where(client_files.c.id.in_(file_ids))).mappings()}
    items += [past_view(r, actor, client, files) for r in past]
    items.sort(key=lambda x: (x['signed_on'] or day_of(x['created'], x['company']['name']) or '', x['created'] or 0), reverse=True)
    # signed BAA by company: every company this person works with, those with agreements first
    _, facts = gather(c, actor, 'all' if client.get('test') else 'real', [cid])
    f = facts.get(cid) or {'companies': [], 'baa': {}, 'status': {}, 'signed': {}}
    names = list(dict.fromkeys(f['companies'] + visible_companies(actor)))
    panel = []
    for name in names:
        b = f['baa'].get(name)
        sv = f['signed'].get((name, 'services'))
        panel.append(company_view(name) | {'linked': name in f['companies'], 'baa': b and {'date': b['date'], 'source': b['source']},
                                           'out': f['status'].get((name, 'baa')) == 'out',
                                           'services': sv and {'date': sv['date'], 'source': sv['source']},
                                           'services_out': f['status'].get((name, 'services')) == 'out'})
    return {'items': items, 'companies': panel, 'covered': bool(f.get('covered'))}


# ------------------------------------------------------------------ past agreements
def past_values(c, actor: dict, client: dict, payload: dict, current: dict | None = None) -> dict:
    cur = current or {}
    entity = payload.get('entity', cur.get('entity'))
    if entity not in ENTITIES:
        raise ValueError('Choose the company the agreement was signed with.')
    sec.ensure_company(actor, entity)
    kind = payload.get('kind', cur.get('kind'))
    if kind not in KINDS:
        raise ValueError('Choose the agreement.')
    source = payload.get('source', cur.get('source'))
    if source not in PAST_SOURCES:
        raise ValueError('Choose how it was signed: DocuSign, Manual (email), Jotform or Other.')
    out = {'entity': entity, 'kind': kind, 'source': source,
           'title': esign.clean(payload.get('title', cur.get('title', '')), 'Title', 200) or KINDS[kind],
           'signed_on': clean_day(payload.get('signed_on', cur.get('signed_on')), 'Signed on'),
           'effective_on': clean_day(payload.get('effective_on', cur.get('effective_on')), 'Effective date'),
           'client_signer': esign.clean(payload.get('client_signer', cur.get('client_signer', '')), 'Client signer', 200),
           'company_signer': esign.clean(payload.get('company_signer', cur.get('company_signer', '')), 'Signed for the company by', 200),
           'reference': esign.clean(payload.get('reference', cur.get('reference', '')), 'Reference', 120),
           'notes': esign.clean(payload.get('notes', cur.get('notes', '')), 'Notes', 4000)}
    if current and 'title' not in payload and current.get('kind') != kind and current.get('title') == KINDS.get(current.get('kind'), ''):
        out['title'] = KINDS[kind]
    if out['signed_on'] and out['signed_on'] > dt.date.today().isoformat():
        raise ValueError('The signed date cannot be in the future.')
    file_id = payload.get('file_id', cur.get('file_id')) or None
    if file_id is not None:
        if not isinstance(file_id, str) or not c.execute(select(client_files.c.id).where(client_files.c.id == file_id, client_files.c.client_id == client['id'])).first():
            raise ValueError('The signed copy was not found on this client. Upload it again.')
    out['file_id'] = file_id
    return out


def log_detail(client: dict, row: dict) -> dict:
    return {'document': row.get('title') or KINDS.get(row.get('kind'), ''), 'entity': row.get('entity'), 'source': SOURCES.get(row.get('source'), ''),
            'signed_on': row.get('signed_on') or '', 'client_number': client.get('client_number') or ''}


def company_people(c) -> set[str]:
    """Our companies' signatories: never the client's signer."""
    import companies
    people = {e.get('signatory', '') for e in ENTITIES.values()}
    people |= {p.get('signatory', '') for p in companies.signatories(c).values()}
    return {p for p in people if p}


def read_file(c, client: dict, file_id: str) -> dict:
    row = c.execute(select(client_files).where(client_files.c.id == file_id, client_files.c.client_id == client['id'])).mappings().first()
    if not row:
        raise HTTPException(404, 'File not found.')
    if not bytes(row['body'][:4]) == b'%PDF':
        return {'file': {'id': row['id'], 'name': row['name'], 'size': row['size'], 'scan': row['scan'], 'pages': 0}, 'found': {'text': False}}
    found = pdf_facts.read(bytes(row['body']), list(ENTITIES.values()), company_people(c), row['name'])
    return {'file': {'id': row['id'], 'name': row['name'], 'size': row['size'], 'scan': row['scan'], 'pages': found['pages']}, 'found': pdf_facts.public(found)}


# ------------------------------------------------------------------ the Signed BAA register
REGISTER_LABEL = {'baa': 'BAA', 'services': 'Services Agreement', 'nda': 'Mutual NDA'}


def baa_register(c, actor: dict, company: str = '', q: str = '', status: str = 'all', page: int = 0, everything: bool = False, kind: str = 'baa') -> dict:
    """The signed agreements register for one agreement type (BAA, Services Agreement or Mutual NDA), one company at a time."""
    kind = kind if kind in TAG_KINDS else 'baa'
    names = visible_companies(actor)
    if company and company not in names:
        raise HTTPException(403, 'You do not have permission for this contracting company.')
    rows, facts = gather(c, actor, 'real')

    def has(r, name):
        return (name, kind) in facts[r['id']]['signed']

    def covered(r):
        f = facts[r['id']]
        return f['covered'] if kind == 'baa' else bool(f['companies']) and all((e, kind) in f['signed'] for e in f['companies'])
    tabs = []
    for name in names:
        linked = [r for r in rows if name in facts[r['id']]['companies']]
        tabs.append(company_view(name) | {'total': len(linked), 'signed': sum(1 for r in linked if has(r, name))})
    if not company:
        company = next((t['name'] for t in tabs if t['total']), names[0] if names else '')
    associated = [r for r in rows if facts[r['id']]['companies'] and any(e in names for e in facts[r['id']]['companies'])]
    n_covered = sum(1 for r in associated if covered(r))
    portal = earlier = 0
    by_source = {s: 0 for s in PAST_SOURCES}
    for r in associated:
        for (e, k), b in facts[r['id']]['signed'].items():
            if k != kind:
                continue
            if b['source'] == 'portal':
                portal += 1
            else:
                earlier += 1
                by_source[b['source']] = by_source.get(b['source'], 0) + 1
    kpis = {'clients': len(associated), 'covered': n_covered, 'coverage': round(n_covered * 100 / len(associated)) if associated else 0,
            'portal': portal, 'earlier': earlier, 'earlier_by_source': by_source, 'missing': len(associated) - n_covered}
    types = {}
    for k in TAG_KINDS:
        states = [doc_states(facts[r['id']])[k] for r in associated]
        types[k] = {'label': KINDS[k], 'short': REGISTER_LABEL[k], 'signed': states.count('signed'), 'out': states.count('out'), 'total': len(associated)}
    linked = [r for r in rows if company in facts[r['id']]['companies'] and matches(r, q)]
    counts = {'all': len(linked), 'signed': sum(1 for r in linked if has(r, company))}
    counts['missing'] = counts['all'] - counts['signed']
    if status == 'signed':
        linked = [r for r in linked if has(r, company)]
    elif status == 'missing':
        linked = [r for r in linked if not has(r, company)]
    linked.sort(key=lambda r: (client_numbers.parse(r['client_number']) or 10 ** 9, r['name'].casefold()))
    total = len(linked)
    if not everything:
        page = max(0, min(int(page or 0), max(0, (total - 1) // PAGE)))
        linked = linked[page * PAGE:(page + 1) * PAGE]
    details = signed_details(c, actor, company, kind, [r['id'] for r in linked if has(r, company)], facts)
    out = []
    for r in linked:
        b = facts[r['id']]['signed'].get((company, kind))
        d = details.get(r['id'], {})
        out.append({'id': r['id'], 'client_number': r['client_number'] or '', 'name': r['name'], 'address': r['address'], 'npi': (r['profile'] or {}).get('npi', ''),
                    'signed': bool(b), 'date': b['date'] if b else '', 'source': b['source'] if b else '', 'signed_by': d.get('signed_by', ''),
                    'effective_on': d.get('effective_on', ''), 'reference': d.get('reference', ''), 'files': d.get('files', []), 'envelope_id': d.get('envelope_id', ''),
                    'client_id': r['id']})
    tab = next((t for t in tabs if t['name'] == company), None)
    return {'kind': kind, 'label': KINDS[kind], 'short': REGISTER_LABEL[kind], 'types': types, 'company': company, 'tabs': tabs, 'kpis': kpis,
            'counts': counts, 'rows': out, 'total': total, 'page': page if not everything else 0, 'page_size': PAGE,
            'coverage': round(tab['signed'] * 100 / tab['total']) if tab and tab['total'] else 0}


def signed_details(c, actor: dict, company: str, kind: str, ids: list[str], facts: dict) -> dict:
    """Signed by, effective date, reference and files of the agreement shown for each client."""
    out: dict[str, dict] = {}
    portal = {cid: facts[cid]['signed'][(company, kind)]['ref'] for cid in ids if facts[cid]['signed'][(company, kind)]['source'] == 'portal'}
    past = {cid: facts[cid]['signed'][(company, kind)]['ref'] for cid in ids if facts[cid]['signed'][(company, kind)]['source'] != 'portal'}
    env_ids = list(portal.values())
    for part in _chunks(env_ids):
        envs = {r['id']: dict(r) for r in c.execute(select(envelopes.c.id, envelopes.c.ref, envelopes.c.contract_id, envelopes.c.client_id, envelopes.c.certificate_sha256)
                                                    .where(envelopes.c.id.in_(part), esign.visible(actor))).mappings()}
        payloads = dict(c.execute(select(db.contracts.c.id, db.contracts.c.payload).where(
            db.contracts.c.id.in_([e['contract_id'] for e in envs.values() if e['contract_id']] or ['']))).all())
        who = signers(c, list(envs))
        docs: dict[str, list] = {}
        for d in c.execute(select(esign_documents.c.id, esign_documents.c.envelope_id, esign_documents.c.name, esign_documents.c.agreement, esign_documents.c.final_sha256)
                           .where(esign_documents.c.envelope_id.in_(list(envs)), esign_documents.c.source != 'attachment')
                           .order_by(esign_documents.c.position)).mappings():
            if kind_of_document(d['agreement'], d['name']) == kind and d['final_sha256']:
                docs.setdefault(d['envelope_id'], []).append(d)
        for e in envs.values():
            files = [{'kind': 'portal', 'id': d['id'], 'name': d['name'], 'url': f"/api/esign/documents/{d['id']}/file?final=1"} for d in docs.get(e['id'], [])]
            out[e['client_id']] = {'signed_by': who.get(e['id'], {}).get('client', ''), 'effective_on': effective_of(payloads.get(e['contract_id']), kind),
                                   'reference': e['ref'], 'files': files, 'envelope_id': e['id'], 'certificate': bool(e['certificate_sha256'])}
    for part in _chunks(list(past.values())):
        for r in c.execute(select(client_agreements.c.id, client_agreements.c.client_id, client_agreements.c.client_signer, client_agreements.c.effective_on,
                                  client_agreements.c.reference, client_agreements.c.file_id, client_files.c.name)
                           .outerjoin(client_files, client_files.c.id == client_agreements.c.file_id).where(client_agreements.c.id.in_(part))).mappings():
            files = [{'kind': 'past', 'id': r['file_id'], 'name': r['name'], 'url': f"/api/clients/files/{r['file_id']}"}] if r['file_id'] else []
            out[r['client_id']] = {'signed_by': r['client_signer'], 'effective_on': r['effective_on'] or '', 'reference': r['reference'], 'files': files}
    return out


def baa_details(c, actor: dict, company: str, ids: list[str], facts: dict) -> dict:
    return signed_details(c, actor, company, 'baa', ids, facts)


def safe_text(value) -> str:
    text = '' if value is None else str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


def export_register(data: dict, fmt: str) -> tuple[bytes, str, str]:
    company = company_view(data['company'])
    short = data.get('short', 'BAA')
    head = ['Client no.', 'Client', 'Company', f'{short} signed', 'Signed on', 'Signed through', 'Signed by', 'Effective date', 'Reference', 'Signed copy',
            'Address', 'NPI']
    rows = [[r['client_number'], r['name'], data['company'], 'Yes' if r['signed'] else 'Not signed', r['date'], SOURCES.get(r['source'], ''), r['signed_by'],
             r['effective_on'], r['reference'], ', '.join(f['name'] or '' for f in r['files']), r['address'], r['npi']] for r in data['rows']]
    stamp = dt.date.today().isoformat()
    filename = f"Signed {short} register - {company['short']} - {stamp}"
    if fmt == 'csv':
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(head)
        for row in rows:
            writer.writerow([safe_text(v) for v in row])
        return ('﻿' + out.getvalue()).encode('utf-8'), 'text/csv; charset=utf-8', filename + '.csv'
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = f'Signed {short} register'[:31]
    ws.append(head)
    for cell in ws[1]:
        cell.fill, cell.font = PatternFill('solid', fgColor='0B2545'), Font(bold=True, color='FFFFFF')
    widths = [len(h) + 2 for h in head]
    for row in rows:
        ws.append([str(v)[:32000] if v is not None else '' for v in row])
        for i, v in enumerate(row):
            cell = ws.cell(row=ws.max_row, column=i + 1)
            cell.data_type = 's'          # text from data, never a formula
            widths[i] = min(60, max(widths[i], len(str(v or '')) + 2))
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'
    info = wb.create_sheet('Report details')
    for line in (('Report', f'Signed {short} register'), ('Company', data['company']), ('Prepared', dt.datetime.now().strftime('%Y-%m-%d %H:%M')),
                 ('Clients', data['total']), ('Signed', sum(1 for r in data['rows'] if r['signed'])), ('Test clients', 'Left out'),
                 ('Confidential', 'Contains client details. Store and share it securely.')):
        info.append(list(line))
    info.column_dimensions['A'].width, info.column_dimensions['B'].width = 16, 70
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename + '.xlsx'


def export_clients(rows: list[dict], facts: dict, fmt: str) -> tuple[bytes, str, str]:
    """The chosen clients from the register: number, name, companies and each agreement's status with its date."""
    state = {'signed': 'Signed', 'out': 'Out for signature', 'none': 'Not signed'}
    head = ['Client no.', 'Client', 'Companies', 'BAA', 'BAA date', 'Services Agreement', 'Services date', 'Mutual NDA', 'NDA date', 'Test client', 'Address', 'NPI']
    data = []
    for r in sorted(rows, key=lambda r: (client_numbers.parse(r['client_number']) or 10 ** 9, r['name'].casefold())):
        f = facts[r['id']]
        line = [r['client_number'] or '', r['name'], ', '.join(company_view(e)['short'] for e in f['companies'])]
        for k in TAG_KINDS:
            v = agreement_view(f, k)
            line += [state[v['state']], v['date'] if v['state'] == 'signed' else v['sent']]
        line += ['Yes' if r['test'] else '', r['address'] or '', (r['profile'] or {}).get('npi', '')]
        data.append(line)
    filename = f"Clients - {dt.date.today().isoformat()}"
    if fmt == 'csv':
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(head)
        for row in data:
            writer.writerow([safe_text(v) for v in row])
        return ('\ufeff' + out.getvalue()).encode('utf-8'), 'text/csv; charset=utf-8', filename + '.csv'
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = 'Clients'
    ws.append(head)
    for cell in ws[1]:
        cell.fill, cell.font = PatternFill('solid', fgColor='0B2545'), Font(bold=True, color='FFFFFF')
    for row in data:
        ws.append([str(v)[:32000] for v in row])
        for i in range(len(row)):
            ws.cell(row=ws.max_row, column=i + 1).data_type = 's'
    ws.freeze_panes = 'A2'
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename + '.xlsx'


def zip_name(value: str) -> str:
    return re.sub(r'[^\w .,()&-]+', '_', value).strip()[:150] or 'file'


def write_zip(c, data: dict, path: str) -> int:
    count = 0
    used: set[str] = set()
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for r in data['rows']:
            if not r['signed']:
                continue
            base = zip_name(f"{r['client_number']} {r['name']} - {data.get('short', 'BAA')} - {r['date'] or 'date unknown'}")
            for i, f in enumerate(r['files']):
                if f['kind'] == 'portal':
                    body = c.execute(select(esign_documents.c.final_pdf).where(esign_documents.c.id == f['id'])).scalar()
                else:
                    body = c.execute(select(client_files.c.body).where(client_files.c.id == f['id'])).scalar()
                if not body:
                    continue
                name = base + (f' ({i + 1})' if i else '') + '.pdf'
                while name in used:
                    name = name[:-4] + ' copy.pdf'
                used.add(name)
                z.writestr(name, bytes(body))
                count += 1
            if r.get('envelope_id'):
                cert = c.execute(select(envelopes.c.certificate).where(envelopes.c.id == r['envelope_id'])).scalar()
                if cert:
                    z.writestr(base + ' - Certificate of Completion.pdf', bytes(cert))
    return count


# ------------------------------------------------------------------ routes
def install(app) -> None:
    def actor_of(request, c):
        return sec.get_actor(request, c)

    @app.get('/api/client-records/register')
    def api_register(request: Request, q: str = '', company: str = '', source: str = '', baa: str = 'all', test: str = 'real', page: int = 0, show: str = 'all'):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            if client_numbers.missing(c):
                client_numbers.ensure_before_reading()
            if company and company not in visible_companies(a):
                raise HTTPException(403, 'You do not have permission for this contracting company.')
            return register(c, a, q[:200], company, source if source in SOURCES else '', baa if baa in ('signed', 'missing') else 'all',
                            test if test in ('test', 'all') else 'real', page, show if show in SHOW else 'all')

    # ---- bulk actions on clients chosen in the register (each checked exactly as the single action is)
    def chosen(payload) -> list[str]:
        ids = payload.get('ids') if isinstance(payload, dict) else None
        if not isinstance(ids, list) or not ids or len(ids) > 500 or not all(isinstance(x, str) and 0 < len(x) <= 64 for x in ids):
            raise HTTPException(400, 'Choose between 1 and 500 clients.')
        return list(dict.fromkeys(ids))

    @app.post('/api/client-records/mark')
    def api_bulk_mark(request: Request, payload: dict = Body(...)):
        import marks
        if not isinstance(payload.get('test'), bool):
            raise HTTPException(400, 'Choose test or real.')
        with esign.write_tx() as c:
            a = actor_of(request, c)
            results = [marks.mark_client(c, a, cid, payload['test']) for cid in chosen(payload)]
            return {'changed': len(results), 'test': payload['test'], 'agreements': sum(r['agreements'] for r in results),
                    'envelopes': sum(r['envelopes'] for r in results)}

    @app.post('/api/client-records/export')
    def api_bulk_export(request: Request, payload: dict = Body(...)):
        with db.engine.begin() as c:
            a = actor_of(request, c)
            ids = chosen(payload)
            rows, facts = gather(c, a, 'all', client_ids=ids)
            if not rows:
                raise HTTPException(404, 'These clients were not found.')
            body, media, name = export_clients(rows, facts, 'csv' if payload.get('format') == 'csv' else 'xlsx')
            db.log(c, a, 'client.register_exported', detail={'clients': len(rows), 'format': 'csv' if payload.get('format') == 'csv' else 'xlsx'})
        return Response(body, media_type=media, headers={'Content-Disposition': f'attachment; filename="{name}"'})

    @app.get('/api/client-records/baa-register')
    def api_baa_register(request: Request, company: str = '', q: str = '', status: str = 'all', page: int = 0, kind: str = 'baa'):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            if client_numbers.missing(c):
                client_numbers.ensure_before_reading()
            return baa_register(c, a, company, q[:200], status if status in ('signed', 'missing') else 'all', page, kind=kind)

    @app.get('/api/client-records/baa-register/export')
    def api_baa_export(request: Request, company: str = '', format: str = 'xlsx', q: str = '', status: str = 'all', kind: str = 'baa'):
        with db.engine.begin() as c:
            a = actor_of(request, c)
            if not company:
                raise HTTPException(400, 'Choose a company.')
            data = baa_register(c, a, company, q[:200], status if status in ('signed', 'missing') else 'all', everything=True, kind=kind)
            body, media, name = export_register(data, 'csv' if format == 'csv' else 'xlsx')
            db.log(c, a, 'client.baa_register_exported', detail={'entity': company, 'kind': data['kind'], 'rows': len(data['rows']),
                                                                 'format': 'csv' if format == 'csv' else 'xlsx'})
        return Response(body, media_type=media, headers={'Content-Disposition': f'attachment; filename="{name}"'})

    @app.get('/api/client-records/baa-register/files.zip')
    def api_baa_zip(request: Request, company: str = '', kind: str = 'baa'):
        with db.engine.begin() as c:
            a = actor_of(request, c)
            if not company:
                raise HTTPException(400, 'Choose a company.')
            data = baa_register(c, a, company, '', 'signed', everything=True, kind=kind)
            fd, path = tempfile.mkstemp(prefix='baa-', suffix='.zip', dir=str(db.DATA))
            os.close(fd)
            try:
                count = write_zip(c, data, path)
            except BaseException:
                os.unlink(path)
                raise
            db.log(c, a, 'client.baa_files_downloaded', detail={'entity': company, 'kind': data['kind'], 'files': count})
        plural = {'baa': 'BAAs', 'services': 'Services Agreements', 'nda': 'Mutual NDAs'}[data['kind']]
        name = f"Signed {plural} - {company_view(company)['short']} - {dt.date.today().isoformat()}.zip".replace('"', '')
        return FileResponse(path, media_type='application/zip', filename=name, background=BackgroundTask(os.unlink, path))

    @app.get('/api/clients/{client_id}/agreements')
    def api_client_agreements(client_id: str, request: Request):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            client = client_profiles.client_for(c, a, client_id)
            out = client_agreement_list(c, a, client)
            out['client'] = {'id': client['id'], 'client_number': client.get('client_number') or '', 'name': client['name']}
            out['can_add'] = True
            out['kinds'], out['sources'] = KINDS, {k: SOURCES[k] for k in PAST_SOURCES}
            return out

    @app.post('/api/clients/{client_id}/past-agreements/read')
    def api_past_read(client_id: str, request: Request, payload: dict = Body(...)):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            client = client_profiles.client_for(c, a, client_id)
            return read_file(c, client, str(payload.get('file_id', ''))[:32])

    @app.post('/api/clients/{client_id}/past-agreements')
    def api_past_add(client_id: str, request: Request, payload: dict = Body(...)):
        with esign.write_tx() as c:
            a = actor_of(request, c)
            client = client_profiles.client_for(c, a, client_id)
            values = past_values(c, a, client, payload)
            t = time.time()
            row = dict(values, id=db.uid(), client_id=client_id, created=t, updated=t, created_by=a['id'], test=bool(client.get('test')))
            c.execute(client_agreements.insert().values(**row))
            c.execute(update(db.clients).where(db.clients.c.id == client_id).values(updated=t))
            db.log(c, a, 'client.past_agreement_added', client_id, log_detail(client, row))
            return past_view(row, a, client, {})

    @app.post('/api/clients/{client_id}/past-agreements/{agreement_id}')
    def api_past_save(client_id: str, agreement_id: str, request: Request, payload: dict = Body(...)):
        with esign.write_tx() as c:
            a = actor_of(request, c)
            client = client_profiles.client_for(c, a, client_id)
            row = c.execute(select(client_agreements).where(client_agreements.c.id == agreement_id, client_agreements.c.client_id == client_id,
                                                            sec.company_scope(a, client_agreements.c.entity))).mappings().first()
            if not row:
                raise HTTPException(404, 'Agreement not found.')
            row = dict(row)
            if not can_change(a, client, row):
                raise HTTPException(403, 'Only the person who added this agreement, the client owner or an administrator can change it.')
            if payload.get('delete') is True:
                c.execute(delete(client_agreements).where(client_agreements.c.id == agreement_id))
                db.log(c, a, 'client.past_agreement_removed', client_id, log_detail(client, row))
                return {'ok': True}
            values = past_values(c, a, client, payload, row)
            values['updated'] = time.time()
            c.execute(update(client_agreements).where(client_agreements.c.id == agreement_id).values(**values))
            changed = sorted(k for k in values if k != 'updated' and values[k] != row.get(k))
            db.log(c, a, 'client.past_agreement_updated', client_id, log_detail(client, row | values) | {'fields': changed})
            return past_view(row | values, a, client, {})

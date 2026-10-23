"""Bulk send (one template to many recipients, one envelope each) and envelope reports.

Bulk send: the template's first signer (lowest signing order) is replaced, row by row, by the people in the list; every
other recipient and every field stay as in the template. {{name}}, {{first_name}} and {{client}} in the subject and
message are filled in for each row. Each envelope is created and sent in its own transaction, so one bad row does not
stop the others; the batch keeps the result of every row.

Reports cover the envelopes the user can see (their own, or their team's for an Admin, or everyone's for the Super
Admin), by sent date.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import statistics
import time
import zoneinfo

from fastapi import Body, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import and_, select, update

import database as db
import esign
import mailer
import security as sec
from esign_db import bulk, envelopes, recipients

MAX_ROWS = 500


def _rows(payload: dict) -> list[dict]:
    rows = payload.get('rows')
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise ValueError(f'Add between 1 and {MAX_ROWS} recipients.')
    out, seen = [], set()
    for i, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f'Row {i} is not valid.')
        name = esign.clean(row.get('name'), f'Name on row {i}', 150, True)
        try:
            email = mailer.valid_email(str(row.get('email') or ''))
        except ValueError:
            raise ValueError(f'Row {i}: enter a valid email address.') from None
        if email.lower() in seen:
            raise ValueError(f'Row {i}: {email} appears more than once.')
        seen.add(email.lower())
        out.append({'row': i, 'name': name, 'email': email, 'client': esign.clean(row.get('client'), f'Client on row {i}', 200),
                    'note': esign.clean(row.get('note'), f'Private message on row {i}', 1000)})
    return out


def _fill(text: str, row: dict) -> str:
    first = row['name'].split()[0] if row['name'].split() else row['name']
    return text.replace('{{name}}', row['name']).replace('{{first_name}}', first).replace('{{client}}', row['client'] or row['name'])


def run(actor_id: str, template_id: str, payload: dict, ip: str, ua: str) -> dict:
    import clients
    rows = _rows(payload)
    with esign.write_tx() as c:
        a = dict(c.execute(select(db.users).where(db.users.c.id == actor_id)).mappings().one())
        template = esign.get_envelope(c, a, template_id)
        if template['status'] != 'template':
            raise HTTPException(400, 'Choose a template for bulk send.')
        first = c.execute(select(recipients).where(recipients.c.envelope_id == template_id, recipients.c.role == 'signer')
                          .order_by(recipients.c.routing_order, recipients.c.created)).mappings().first()
        if not first:
            raise HTTPException(400, 'The template needs a recipient who signs: each person in the list takes that place.')
        try:
            mailer.transport(c, template['entity'])
        except mailer.MailboxNotReady as exc:
            raise HTTPException(409, str(exc)) from None
        batch = dict(id=db.uid(), owner_id=a['id'], template_id=template_id, entity=template['entity'],
                     name=esign.clean(payload.get('name'), 'Batch name', 200) or f"{template['template_name']} · {len(rows)} recipients",
                     created=time.time(), total=len(rows), sent=0, failed=0, results=[])
        c.execute(bulk.insert().values(**batch))
        db.log(c, a, 'esign.bulk_started', None, {'batch': batch['id'], 'template': template['template_name'], 'rows': len(rows)})
    results = []
    for row in rows:
        try:
            with esign.write_tx() as c:
                a = dict(c.execute(select(db.users).where(db.users.c.id == actor_id)).mappings().one())
                template = esign.get_envelope(c, a, template_id)
                client_id = clients.ensure_client(c, a, row['client'], template['entity'], row['email'], row['name']) if row['client'] else None
                target = esign.copy(c, a, template, client_id=client_id)
                values = {'bulk_id': batch['id'], 'subject': _fill(template['subject'], row)[:200], 'message': _fill(template['message'], row)[:4000]}
                if client_id:
                    values['client_id'] = client_id
                c.execute(update(envelopes).where(envelopes.c.id == target['id']).values(**values))
                signer = c.execute(select(recipients.c.id, recipients.c.note).where(recipients.c.envelope_id == target['id'], recipients.c.role == 'signer')
                                   .order_by(recipients.c.routing_order, recipients.c.created)).first()
                c.execute(update(recipients).where(recipients.c.id == signer[0]).values(name=row['name'], email=row['email'], note=row['note'] or signer[1]))
                envelope = esign.lock(c, target['id'])
                esign.send(c, a, envelope, ip, ua)
                results.append({'row': row['row'], 'name': row['name'], 'email': row['email'], 'envelope': target['id'], 'ref': target['ref'], 'error': ''})
        except (HTTPException, ValueError) as exc:
            results.append({'row': row['row'], 'name': row['name'], 'email': row['email'], 'envelope': '', 'ref': '',
                            'error': str(exc.detail if isinstance(exc, HTTPException) else exc)[:300]})
    with esign.write_tx() as c:
        sent = sum(1 for r in results if not r['error'])
        c.execute(update(bulk).where(bulk.c.id == batch['id']).values(results=results, sent=sent, failed=len(results) - sent))
    return dict(batch, results=results, sent=sent, failed=len(results) - sent)


def report(c, actor: dict, since: float, until: float, entity: str) -> dict:
    cond = and_(esign.visible(actor), envelopes.c.status.notin_(('template', 'draft')), envelopes.c.sent_at >= since, envelopes.c.sent_at < until)
    if entity:
        cond = and_(cond, envelopes.c.entity == entity)
    rows = [dict(r) for r in c.execute(select(envelopes.c.id, envelopes.c.ref, envelopes.c.subject, envelopes.c.status, envelopes.c.entity, envelopes.c.owner_id,
                                              envelopes.c.sent_at, envelopes.c.completed_at, envelopes.c.expires_at, envelopes.c.close_reason, envelopes.c.client_id)
                                       .where(cond).order_by(envelopes.c.sent_at).limit(20000)).mappings()]
    owners = dict(c.execute(select(db.users.c.id, db.users.c.name).where(db.users.c.id.in_({r['owner_id'] for r in rows} or {''}))).all())
    statuses: dict[str, int] = {}
    by_company: dict[str, dict] = {}
    by_sender: dict[str, dict] = {}
    months: dict[str, dict] = {}
    hours = []
    for r in rows:
        statuses[r['status']] = statuses.get(r['status'], 0) + 1
        for table, key in ((by_company, r['entity']), (by_sender, owners.get(r['owner_id'], ''))):
            entry = table.setdefault(key, {'sent': 0, 'completed': 0, 'waiting': 0, 'declined': 0})
            entry['sent'] += 1
            if r['status'] == 'completed':
                entry['completed'] += 1
            elif r['status'] in ('sent', 'completing'):
                entry['waiting'] += 1
            elif r['status'] == 'declined':
                entry['declined'] += 1
        month = dt.datetime.fromtimestamp(r['sent_at'], dt.UTC).strftime('%Y-%m')
        m = months.setdefault(month, {'sent': 0, 'completed': 0})
        m['sent'] += 1
        if r['status'] == 'completed':
            m['completed'] += 1
            hours.append((r['completed_at'] - r['sent_at']) / 3600)
    sent = len(rows)
    completed = statuses.get('completed', 0)
    t = time.time()
    waiting = sorted((r for r in rows if r['status'] == 'sent'), key=lambda r: r['sent_at'])[:10]
    declined = [r for r in rows if r['status'] == 'declined'][-10:]
    return {'since': since, 'until': until, 'entity': entity, 'sent': sent, 'statuses': statuses, 'completed': completed,
            'completion_rate': round(completed / sent * 100, 1) if sent else 0,
            'hours_to_complete': {'average': round(sum(hours) / len(hours), 1) if hours else None, 'median': round(statistics.median(hours), 1) if hours else None},
            'expiring_soon': sum(1 for r in rows if r['status'] == 'sent' and r['expires_at'] and r['expires_at'] <= t + 7 * 86400),
            'by_company': [dict(v, name=k) for k, v in sorted(by_company.items())], 'by_sender': [dict(v, name=k) for k, v in sorted(by_sender.items())],
            'months': [dict(v, month=k) for k, v in sorted(months.items())],
            'oldest_waiting': [{'id': r['id'], 'ref': r['ref'], 'subject': r['subject'], 'sent_at': r['sent_at'], 'days': round((t - r['sent_at']) / 86400, 1)}
                               for r in waiting],
            'recent_declines': [{'id': r['id'], 'ref': r['ref'], 'subject': r['subject'], 'reason': r['close_reason'] or ''} for r in declined]}


def _cell(value) -> str:
    text = '' if value is None else str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text   # spreadsheet formula injection


def envelopes_csv(c, actor: dict, since: float, until: float, entity: str) -> bytes:
    cond = and_(esign.visible(actor), envelopes.c.status.notin_(('template', 'draft')), envelopes.c.sent_at >= since, envelopes.c.sent_at < until)
    if entity:
        cond = and_(cond, envelopes.c.entity == entity)
    rows = [dict(r) for r in c.execute(select(envelopes).where(cond).order_by(envelopes.c.sent_at).limit(20000)).mappings()]
    ids = [r['id'] for r in rows]
    people: dict[str, list] = {}
    if ids:
        for r in c.execute(select(recipients.c.envelope_id, recipients.c.name, recipients.c.email, recipients.c.role, recipients.c.status, recipients.c.signed_at)
                           .where(recipients.c.envelope_id.in_(ids)).order_by(recipients.c.routing_order)).mappings():
            people.setdefault(r['envelope_id'], []).append(r)
    owners = dict(c.execute(select(db.users.c.id, db.users.c.name).where(db.users.c.id.in_({r['owner_id'] for r in rows} or {''}))).all())
    names = dict(c.execute(select(db.clients.c.id, db.clients.c.name).where(db.clients.c.id.in_({r['client_id'] for r in rows if r['client_id']} or {''}))).all())
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['Envelope ID', 'Subject', 'Company', 'Client', 'Sender', 'Status', 'Sent (UTC)', 'Completed (UTC)', 'Hours to complete', 'Recipients',
                'Declined or voided reason'])
    stamp = lambda ts: dt.datetime.fromtimestamp(ts, zoneinfo.ZoneInfo('UTC')).strftime('%Y-%m-%d %H:%M') if ts else ''  # noqa: E731
    for r in rows:
        who = '; '.join(f"{p['name']} <{p['email']}> ({esign.ROLE_LABELS.get(p['role'], p['role'])}: {p['status']})" for p in people.get(r['id'], []))
        took = round((r['completed_at'] - r['sent_at']) / 3600, 1) if r['completed_at'] and r['sent_at'] else ''
        w.writerow([_cell(v) for v in (r['ref'], r['subject'], r['entity'], names.get(r['client_id'], ''), owners.get(r['owner_id'], ''), r['status'],
                                       stamp(r['sent_at']), stamp(r['completed_at']), took, who, r['close_reason'] or '')])
    return out.getvalue().encode('utf-8-sig')


def _range(since: float, until: float) -> tuple[float, float]:
    t = time.time()
    until = until or t + 86400
    since = since or until - 90 * 86400
    if since >= until or until - since > 3 * 366 * 86400:
        raise ValueError('Choose a date range of at most three years.')
    return since, until


def install(app) -> None:
    @app.post('/api/esign/bulk')
    def api_bulk(request: Request, payload: dict = Body(...)):
        with db.engine.connect() as c:
            a = sec.get_actor(request, c)
        ip, ua = esign._actor_info(request)
        return run(a['id'], str(payload.get('template_id') or ''), payload, ip, ua)

    @app.get('/api/esign/bulk')
    def api_bulk_list(request: Request):
        with db.engine.connect() as c:
            a = sec.get_actor(request, c)
            rows = c.execute(select(bulk).where(sec.scope(a, bulk.c.owner_id), sec.company_scope(a, bulk.c.entity)).order_by(bulk.c.created.desc()).limit(50)).mappings()
            return {'batches': [{k: r[k] for k in ('id', 'name', 'entity', 'created', 'total', 'sent', 'failed', 'results')} for r in rows]}

    @app.get('/api/esign/reports')
    def api_reports(request: Request, since: float = 0, until: float = 0, entity: str = ''):
        since, until = _range(since, until)
        with db.engine.connect() as c:
            return report(c, sec.get_actor(request, c), since, until, entity[:100])

    @app.get('/api/esign/reports/envelopes.csv')
    def api_reports_csv(request: Request, since: float = 0, until: float = 0, entity: str = ''):
        since, until = _range(since, until)
        with db.engine.begin() as c:
            a = sec.get_actor(request, c)
            body = envelopes_csv(c, a, since, until, entity[:100])
            db.log(c, a, 'esign.report_exported', None, {'since': since, 'until': until, 'entity': entity[:100]})
        return Response(body, media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="TFSC envelopes.csv"'})

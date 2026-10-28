"""Home figures, Insights, the report builder (on screen, Excel and CSV), saved reports and the activity log.

Every figure and row respects the same scope as the rest of the portal: a User sees their own records, an Admin their
team's (themselves and the users assigned to them), the Super Admin everything; company permissions always apply.
The activity log and the activity and users reports are for Admins and above.

The activity log merges two sources: what people did in the portal (the audit table: sign-in, clients, agreements,
envelopes, downloads, exports, settings) and what signers did on the signing page (the envelope audit trail: opened,
viewed, signed, declined, downloaded). Signers have no account, so their entries carry their name and email.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import statistics
import time
import zoneinfo

from fastapi import Body, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import and_, func, or_, select

import database as db
import esign
import security as sec
from entities import ENTITIES, get_entity
from esign_db import envelopes, events, recipients
from esign_db import documents as esign_documents

EXPORT_LIMIT = 50_000
SCREEN_LIMIT = 10_000
STATUS_LABELS = {'draft': 'Draft', 'sent': 'Waiting for others', 'completing': 'Finishing', 'completed': 'Completed', 'declined': 'Declined',
                 'voided': 'Voided', 'expired': 'Expired', 'template': 'Template'}
RECIPIENT_STATUS = {'created': 'Waiting for their turn', 'sent': 'Sent', 'delivered': 'Viewed', 'signed': 'Signed', 'completed': 'Completed',
                    'declined': 'Declined', 'auth_failed': 'Locked (wrong access code)', 'reassigned': 'Assigned to someone else'}
ROLE_LABELS = {'signer': 'Needs to sign', 'in_person': 'Signs in person', 'viewer': 'Needs to view', 'cc': 'Receives a copy'}
AGREEMENTS = {'baa': 'BAA', 'services': 'Services Agreement', 'nda': 'Mutual NDA'}

# What each audit action means, for people reading the log.
ACTIONS = {
    'user.signed_in': 'Signed in', 'user.sso_signed_in': 'Signed in with single sign-on', 'user.signed_out': 'Signed out',
    'user.password_changed': 'Changed their password', 'user.created': 'Created a user', 'user.permissions_changed': "Changed a user's access",
    'workspace.initialized': 'Set up the workspace', 'workspace.updated': 'Changed workspace settings', 'company.updated': 'Changed company settings',
    'signature.updated': 'Replaced a company signature', 'email_trust.checked': 'Checked email trust records',
    'mailbox.updated': 'Changed a company mailbox', 'mailbox.tested': 'Tested a company mailbox',
    'client.created': 'Created a client', 'client.updated': 'Updated a client', 'client.viewed': 'Opened a client profile',
    'client.contact_added': 'Added a client contact', 'client.contact_removed': 'Removed a client contact',
    'client.file_uploaded': 'Uploaded a file to a client', 'client.file_downloaded': 'Downloaded a client file', 'client.file_removed': 'Removed a client file',
    'contract.previewed': 'Previewed agreement wording', 'contract.queued': 'Requested agreement generation', 'contract.generation_started': 'Generation started',
    'contract.generated': 'Generated agreements (Word and PDF)', 'contract.failed': 'Agreement generation failed',
    'document.downloaded': 'Downloaded a generated agreement',
    'esign.created': 'Created an envelope', 'esign.sent': 'Sent an envelope', 'esign.viewed': 'Opened an envelope', 'esign.resent': 'Resent an envelope',
    'esign.corrected': 'Corrected an envelope', 'esign.voided': 'Voided an envelope', 'esign.deleted': 'Deleted a draft', 'esign.copied': 'Copied an envelope',
    'esign.template_saved': 'Saved a template', 'esign.document_added': 'Added a document to an envelope',
    'esign.completed': 'Envelope completed', 'esign.declined': 'Declined to sign', 'esign.expired': 'Envelope expired', 'esign.reassigned': 'Assigned the envelope to someone else',
    'esign.document_downloaded': 'Downloaded an envelope document', 'esign.certificate_downloaded': 'Downloaded a Certificate of Completion',
    'esign.completed_downloaded': 'Downloaded the completed documents', 'esign.attachment_downloaded': 'Downloaded an email attachment',
    'esign.links_off': 'Turned off recipient links', 'esign.links_on': 'Turned recipient links back on',
    'esign.bulk_started': 'Started a bulk send', 'esign.report_exported': 'Exported an envelope report',
    'report.exported': 'Exported a report', 'activity.exported': 'Exported the activity log',
    'agreement.review_submitted': 'Submitted an agreement review', 'identity.account_linked': 'Linked a sign-in account',
    'crm.handoff_received': 'Opened from the CRM', 'crm.handoff_redeemed': 'Opened from the CRM', 'crm.account_provisioned': 'Account created from the CRM',
    'crm.actor_linked': 'Linked to the CRM', 'legacy.client_imported': 'Imported a client', 'legacy.contract_imported': 'Imported an agreement',
}
# Audit entries written for something a signer did; the envelope audit trail already has them (with more detail).
MIRRORED = ('esign.declined', 'esign.reassigned')
# Envelope audit-trail entries shown in the activity log: what signers did, and what the application did for them.
SIGNER_EVENTS = ('delivered', 'consent', 'auth_passed', 'auth_failed', 'locked', 'adopted', 'signed', 'reviewed', 'declined', 'saved', 'reassigned',
                 'attachment', 'downloaded', 'replied', 'bounced', 'portal_session', 'host_session')
CATEGORIES = {'access': ('user.', 'identity.', 'workspace.initialized'), 'clients': ('client.',), 'agreements': ('contract.', 'document.', 'agreement.'),
              'envelopes': ('esign.',), 'settings': ('company.', 'workspace.updated', 'mailbox.', 'signature.', 'email_trust.'),
              'exports': ('report.', 'activity.', 'esign.report_exported')}


# ------------------------------------------------------------------ helpers
def tz_of(name: str | None):
    try:
        return zoneinfo.ZoneInfo(name or 'UTC')
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return zoneinfo.ZoneInfo('UTC')


def as_time(ts, tz) -> dt.datetime | None:
    return dt.datetime.fromtimestamp(ts, tz).replace(tzinfo=None) if ts else None


def text_time(ts, tz) -> str:
    return dt.datetime.fromtimestamp(ts, tz).strftime('%Y-%m-%d %H:%M') if ts else ''


def admin_only(actor: dict) -> None:
    if actor['role'] == 'user':
        raise HTTPException(403, 'Administrator access required.')


def team(actor: dict):
    """Ids of the users whose work the actor may see (None: everyone)."""
    if actor['role'] == 'super_admin':
        return None
    if actor['role'] == 'admin':
        return select(db.users.c.id).where(or_(db.users.c.id == actor['id'], db.users.c.admin_id == actor['id']))
    return select(db.users.c.id).where(db.users.c.id == actor['id'])


def browser(agent: str | None) -> str:
    if not agent:
        return ''
    name = next((label for pattern, label in ((r'Edg/', 'Edge'), (r'OPR/|Opera', 'Opera'), (r'Firefox/', 'Firefox'), (r'Chrome/', 'Chrome'),
                                              (r'Safari/', 'Safari')) if re.search(pattern, agent)), 'Browser')
    system = next((label for pattern, label in ((r'iPhone|iPad', 'iOS'), (r'Android', 'Android'), (r'Windows', 'Windows'), (r'Mac OS X|Macintosh', 'macOS'),
                                                (r'Linux', 'Linux')) if re.search(pattern, agent)), '')
    if re.search(r'python|httpx|curl|testclient', agent, re.I):
        return 'Automated client'
    return f'{name} on {system}' if system else name


def describe(action: str, detail: dict) -> str:
    """One readable line of what an audit entry did, from its detail (which is data, shown as text)."""
    d = detail or {}
    parts = []
    for key in ('document', 'file', 'subject', 'client', 'envelope', 'entity', 'to', 'reason', 'template', 'name', 'report', 'domain'):
        value = d.get(key)
        if value and isinstance(value, (str, int, float)):
            parts.append(f"{key.replace('_', ' ')}: {value}" if key in ('envelope', 'to', 'reason', 'domain') else str(value))
    if action == 'company.updated' and d.get('changed'):
        parts.append('changed ' + ', '.join(str(x) for x in d['changed']))
    if action == 'contract.queued' and d.get('entity'):
        parts = [d['entity']]
    if action == 'esign.completed' and d.get('documents'):
        parts.append(', '.join(x.get('name', '') for x in d['documents'] if isinstance(x, dict)))
    if d.get('version') == 'completed':
        parts.append('completed copy')
    return ' · '.join(dict.fromkeys(p for p in parts if p))[:400]


def envelope_scope(actor: dict, entity: str = ''):
    cond = esign.visible(actor)
    return and_(cond, envelopes.c.entity == entity) if entity else cond


# ------------------------------------------------------------------ Home
def home(c, actor: dict, entity: str) -> dict:
    t = time.time()
    since = t - 183 * 86400
    scope = envelope_scope(actor, entity)

    def count(*cond):
        return c.execute(select(func.count()).select_from(envelopes).where(scope, *cond)).scalar() or 0
    action_cond = [envelopes.c.status == 'sent', sec.company_scope(actor, envelopes.c.entity), envelopes.c.id.in_(esign.waiting_for(actor))]
    if entity:
        action_cond.append(envelopes.c.entity == entity)
    status = {'action': c.execute(select(func.count()).select_from(envelopes).where(*action_cond)).scalar() or 0,
              'waiting': count(envelopes.c.status.in_(('sent', 'completing'))),
              'expiring': count(envelopes.c.status == 'sent', envelopes.c.expires_at <= t + 7 * 86400),
              'completed': count(envelopes.c.status == 'completed', envelopes.c.completed_at >= since)}
    sent = count(envelopes.c.sent_at >= since)
    completed_of_sent = count(envelopes.c.sent_at >= since, envelopes.c.status == 'completed')
    pending_signatures = c.execute(select(func.count()).select_from(recipients).join(envelopes, envelopes.c.id == recipients.c.envelope_id).where(
        scope, envelopes.c.status == 'sent', recipients.c.role.in_(esign.SIGNING_ROLES), recipients.c.status.notin_(esign.DONE_STATUSES + esign.INACTIVE + ('declined',)))).scalar() or 0
    contract_cond = [sec.scope(actor, db.contracts.c.owner_id), sec.company_scope(actor, db.contracts.c.entity), db.contracts.c.created >= since]
    if entity:
        contract_cond.append(db.contracts.c.entity == entity)
    generated = c.execute(select(func.count()).select_from(db.contracts).where(*contract_cond)).scalar() or 0
    totals = {'sent': sent, 'pending_signatures': pending_signatures, 'completed': completed_of_sent, 'drafts': count(envelopes.c.status == 'draft'),
              'completion_rate': round(completed_of_sent * 100 / sent) if sent else None, 'generated': generated}
    recent = []
    rows = c.execute(select(events.c.kind, events.c.at, events.c.actor, events.c.recipient_id, envelopes.c.id, envelopes.c.ref, envelopes.c.subject,
                            envelopes.c.entity, envelopes.c.status).join(envelopes, envelopes.c.id == events.c.envelope_id)
                     .where(scope, events.c.kind.in_(('sent', 'delivered', 'signed', 'reviewed', 'completed', 'declined', 'voided', 'downloaded')))
                     .order_by(events.c.at.desc()).limit(14)).mappings().all()
    for r in rows:
        recent.append({'kind': r['kind'], 'label': esign.LABELS.get(r['kind'], r['kind']), 'at': r['at'], 'who': r['actor'], 'envelope_id': r['id'],
                       'ref': r['ref'], 'subject': r['subject'], 'entity': r['entity'], 'status': r['status']})
    return {'status': status, 'totals': totals, 'recent': recent, 'since': since, 'user': actor['name']}


# ------------------------------------------------------------------ Insights
def month_starts(months: int, tz) -> list[dt.datetime]:
    today = dt.datetime.now(tz)
    start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    out = [start]
    for _ in range(months - 1):
        start = (start - dt.timedelta(days=1)).replace(day=1)
        out.append(start)
    return list(reversed(out))


def insights(c, actor: dict, entity: str, months: int, tz_name: str) -> dict:
    tz = tz_of(tz_name)
    months = max(3, min(int(months or 6), 24))
    starts = month_starts(months, tz)
    since = starts[0].timestamp()
    t = time.time()
    keys = [s.strftime('%Y-%m') for s in starts]
    month_of = lambda ts: dt.datetime.fromtimestamp(ts, tz).strftime('%Y-%m')  # noqa: E731
    scope = envelope_scope(actor, entity)
    rows = c.execute(select(envelopes.c.id, envelopes.c.entity, envelopes.c.status, envelopes.c.sent_at, envelopes.c.completed_at, envelopes.c.closed_at,
                            envelopes.c.client_id).where(scope, envelopes.c.status.notin_(('template', 'draft')),
                                                         or_(envelopes.c.sent_at >= since, envelopes.c.completed_at >= since, envelopes.c.status.in_(('sent', 'completing')))))
    rows = [dict(r) for r in rows.mappings()]
    monthly = {k: {'month': k, 'sent': 0, 'completed': 0, 'declined': 0, 'generated': 0} for k in keys}
    by_company = {name: {'entity': name, 'short': ENTITIES[name]['short'], 'sent': 0, 'completed': 0, 'waiting': 0} for name in ENTITIES
                  if actor['role'] == 'super_admin' or name in actor['companies']}
    turnaround, ages, clients = [], {'0-2 days': 0, '3-7 days': 0, '8-14 days': 0, '15-30 days': 0, 'Over 30 days': 0}, {}
    for r in rows:
        if r['sent_at'] and r['sent_at'] >= since:
            monthly[month_of(r['sent_at'])]['sent'] += 1
            if r['entity'] in by_company:
                by_company[r['entity']]['sent'] += 1
            if r['client_id']:
                clients[r['client_id']] = clients.get(r['client_id'], 0) + 1
        if r['status'] == 'completed' and r['completed_at'] and r['completed_at'] >= since:
            monthly[month_of(r['completed_at'])]['completed'] += 1
            if r['entity'] in by_company:
                by_company[r['entity']]['completed'] += 1
            if r['sent_at']:
                turnaround.append((r['completed_at'] - r['sent_at']) / 86400)
        if r['status'] in ('declined', 'voided', 'expired') and r['closed_at'] and r['closed_at'] >= since:
            monthly[month_of(r['closed_at'])]['declined'] += 1
        if r['status'] in ('sent', 'completing') and r['sent_at']:
            days = (t - r['sent_at']) / 86400
            bucket = '0-2 days' if days < 3 else '3-7 days' if days < 8 else '8-14 days' if days < 15 else '15-30 days' if days <= 30 else 'Over 30 days'
            ages[bucket] += 1
            if r['entity'] in by_company:
                by_company[r['entity']]['waiting'] += 1
    contract_cond = [sec.scope(actor, db.contracts.c.owner_id), sec.company_scope(actor, db.contracts.c.entity), db.contracts.c.created >= since]
    if entity:
        contract_cond.append(db.contracts.c.entity == entity)
    for (created,) in c.execute(select(db.contracts.c.created).where(*contract_cond)):
        monthly[month_of(created)]['generated'] += 1
    names = dict(c.execute(select(db.clients.c.id, db.clients.c.name).where(db.clients.c.id.in_(list(clients) or ['']))).all())
    top = sorted(({'client': names.get(k, 'Unknown client'), 'client_id': k, 'sent': v} for k, v in clients.items()), key=lambda x: -x['sent'])[:6]
    signer_rows = c.execute(select(recipients.c.status, recipients.c.delivered_at, recipients.c.signed_at).join(envelopes, envelopes.c.id == recipients.c.envelope_id)
                            .where(scope, envelopes.c.sent_at >= since, recipients.c.role.in_(esign.SIGNING_ROLES), recipients.c.status != 'created')).all()
    funnel = {'sent': len(signer_rows), 'viewed': sum(1 for r in signer_rows if r[1] or r[2]), 'signed': sum(1 for r in signer_rows if r[2] and r[0] in esign.DONE_STATUSES)}
    sent_total = sum(m['sent'] for m in monthly.values())
    completed_total = sum(m['completed'] for m in monthly.values())
    return {'months': [monthly[k] | {'label': starts[i].strftime('%b %Y')} for i, k in enumerate(keys)], 'by_company': list(by_company.values()),
            'pending_age': [{'bucket': k, 'count': v} for k, v in ages.items()], 'top_clients': top, 'funnel': funnel,
            'totals': {'sent': sent_total, 'completed': completed_total, 'waiting': sum(ages.values()),
                       'generated': sum(m['generated'] for m in monthly.values()),
                       'completion_rate': round(completed_total * 100 / sent_total) if sent_total else None,
                       'avg_days': round(statistics.mean(turnaround), 1) if turnaround else None,
                       'median_days': round(statistics.median(turnaround), 1) if turnaround else None},
            'since': since}


# ------------------------------------------------------------------ reports
# (key, label, type) — type: text | datetime | number
DATASETS = {
    'agreement_log': {'label': 'Agreement log', 'admin': False, 'date': 'Sent',
                      'columns': [('source', 'Source', 'text'), ('client', 'Client / Practice', 'text'),
                                  ('baa', 'BAA', 'bool'), ('sa', 'SA', 'bool'), ('nda', 'NDA', 'bool'), ('pa', 'PA', 'bool'),
                                  ('electronic', 'Electronic', 'bool'), ('executed', 'Executed', 'bool'),
                                  ('sent', 'Sent date', 'datetime'), ('sender', 'Sent by', 'text'), ('status', 'Status', 'text'),
                                  ('completed', 'Completed', 'datetime'), ('ref', 'Reference', 'text')],
                      'default': ['source', 'client', 'baa', 'sa', 'nda', 'pa', 'electronic', 'executed', 'sent', 'sender', 'status', 'completed']},
    'envelopes': {'label': 'Envelopes', 'admin': False, 'date': 'Sent (or created, for drafts)',
                  'columns': [('ref', 'Envelope ID', 'text'), ('subject', 'Subject', 'text'), ('company', 'Company', 'text'), ('client', 'Client', 'text'),
                              ('sender', 'Sender', 'text'), ('status', 'Status', 'text'), ('created', 'Created', 'datetime'), ('sent', 'Sent', 'datetime'),
                              ('completed', 'Completed', 'datetime'), ('days_to_complete', 'Days to complete', 'number'), ('recipients', 'Recipients', 'text'),
                              ('progress', 'Signed', 'text'), ('waiting_on', 'Waiting on', 'text'), ('documents', 'Documents', 'text'),
                              ('expires', 'Expires', 'datetime'), ('last_change', 'Last change', 'datetime')],
                  'default': ['ref', 'subject', 'company', 'client', 'sender', 'status', 'sent', 'completed', 'progress', 'waiting_on']},
    'recipients': {'label': 'Recipients', 'admin': False, 'date': 'Envelope sent',
                   'columns': [('ref', 'Envelope ID', 'text'), ('subject', 'Subject', 'text'), ('company', 'Company', 'text'), ('client', 'Client', 'text'),
                               ('sender', 'Sender', 'text'), ('envelope_status', 'Envelope status', 'text'), ('name', 'Recipient', 'text'),
                               ('email', 'Email', 'text'), ('role', 'Action', 'text'), ('order', 'Order', 'number'), ('status', 'Status', 'text'),
                               ('sent', 'Sent', 'datetime'), ('viewed', 'Viewed', 'datetime'), ('signed', 'Signed / done', 'datetime'),
                               ('declined_reason', 'Decline reason', 'text'), ('delivery', 'Email delivery', 'text'), ('reminders', 'Reminders', 'number'),
                               ('ip', 'IP address', 'text'), ('authentication', 'Authentication', 'text')],
                   'default': ['ref', 'subject', 'client', 'name', 'email', 'role', 'status', 'sent', 'viewed', 'signed']},
    'agreements': {'label': 'Generated agreements', 'admin': False, 'date': 'Generated',
                   'columns': [('contract', 'Agreement reference', 'text'), ('client', 'Client', 'text'), ('company', 'Company', 'text'),
                               ('created', 'Generated', 'datetime'), ('created_by', 'Generated by', 'text'), ('documents', 'Documents', 'text'),
                               ('effective_date', 'Effective date', 'text'), ('governing_state', 'Governing law', 'text'), ('services', 'Services', 'text'),
                               ('signatory', 'Company signatory', 'text'), ('esign', 'Sent for signature', 'text'), ('signed', 'Signed', 'datetime')],
                   'default': ['contract', 'client', 'company', 'created', 'created_by', 'documents', 'effective_date', 'esign', 'signed']},
    'clients': {'label': 'Clients', 'admin': False, 'date': 'Client created',
                'columns': [('client', 'Client', 'text'), ('address', 'Address', 'text'), ('phone', 'Phone', 'text'), ('email', 'Email', 'text'),
                            ('owner', 'Owner', 'text'), ('created', 'Created', 'datetime'), ('updated', 'Updated', 'datetime'),
                            ('agreements', 'Agreements generated', 'number'), ('envelopes', 'Envelopes sent', 'number'), ('completed', 'Completed', 'number'),
                            ('waiting', 'Waiting for signature', 'number'), ('last_sent', 'Last sent', 'datetime')],
                'default': ['client', 'owner', 'created', 'agreements', 'envelopes', 'completed', 'waiting', 'last_sent']},
    'activity': {'label': 'User activity', 'admin': True, 'date': 'Time',
                 'columns': [('at', 'Time', 'datetime'), ('who', 'Who', 'text'), ('kind', 'Type', 'text'), ('action', 'Action', 'text'),
                             ('detail', 'Details', 'text'), ('client', 'Client', 'text'), ('ip', 'IP address', 'text'), ('browser', 'Browser', 'text')],
                 'default': ['at', 'who', 'action', 'detail', 'client', 'ip', 'browser']},
    'users': {'label': 'Users', 'admin': True, 'date': 'User created',
              'columns': [('name', 'Name', 'text'), ('email', 'Email', 'text'), ('role', 'Role', 'text'), ('companies', 'Companies', 'text'),
                          ('admin', 'Assigned Admin', 'text'), ('status', 'Status', 'text'), ('created', 'Created', 'datetime'),
                          ('last_sign_in', 'Last sign-in', 'datetime'), ('agreements', 'Agreements generated', 'number'),
                          ('envelopes', 'Envelopes sent', 'number'), ('completed', 'Envelopes completed', 'number')],
              'default': ['name', 'email', 'role', 'companies', 'status', 'last_sign_in', 'agreements', 'envelopes', 'completed']},
}
REPORT_STATUSES = {'waiting': ('sent', 'completing'), 'completed': ('completed',), 'draft': ('draft',), 'declined': ('declined',), 'voided': ('voided',),
                   'expired': ('expired',)}


def filters_of(payload: dict) -> dict:
    f = payload.get('filters') or {}
    if not isinstance(f, dict):
        raise ValueError('Invalid report filters.')
    out = {}
    for key in ('since', 'until'):
        try:
            out[key] = float(f[key]) if f.get(key) not in (None, '') else None
        except (TypeError, ValueError):
            raise ValueError('Invalid date range.') from None
    out['entity'] = str(f.get('entity') or '')[:100]
    if out['entity'] and out['entity'] not in ENTITIES:
        raise ValueError('Unknown company.')
    out['status'] = str(f.get('status') or '')
    if out['status'] and out['status'] not in REPORT_STATUSES:
        raise ValueError('Unknown status.')
    out['owner'] = str(f.get('owner') or '')[:32]
    out['client'] = str(f.get('client') or '').strip()[:200]
    out['tz'] = str(f.get('tz') or 'UTC')[:60]
    return out


def _range(column, f):
    cond = []
    if f['since']:
        cond.append(column >= f['since'])
    if f['until']:
        cond.append(column < f['until'])
    return cond


def _owners(c, ids) -> dict:
    ids = {i for i in ids if i}
    return dict(c.execute(select(db.users.c.id, db.users.c.name).where(db.users.c.id.in_(ids or {''}))).all())


def _client_names(c, ids) -> dict:
    ids = {i for i in ids if i}
    return dict(c.execute(select(db.clients.c.id, db.clients.c.name).where(db.clients.c.id.in_(ids or {''}))).all())


def _client_filter(column, f):
    if not f['client']:
        return []
    return [column.in_(select(db.clients.c.id).where(func.lower(db.clients.c.name).contains(f['client'].lower(), autoescape=True)))]


def rows_envelopes(c, actor, f, tz):
    cond = [envelope_scope(actor, f['entity']), envelopes.c.status != 'template', *_range(func.coalesce(envelopes.c.sent_at, envelopes.c.created), f),
            *_client_filter(envelopes.c.client_id, f)]
    if f['status']:
        cond.append(envelopes.c.status.in_(REPORT_STATUSES[f['status']]))
    if f['owner']:
        cond.append(envelopes.c.owner_id == f['owner'])
    rows = [dict(r) for r in c.execute(select(envelopes.c.id, envelopes.c.ref, envelopes.c.subject, envelopes.c.entity, envelopes.c.client_id, envelopes.c.owner_id,
                                              envelopes.c.status, envelopes.c.created, envelopes.c.sent_at, envelopes.c.completed_at, envelopes.c.expires_at,
                                              envelopes.c.updated).where(*cond).order_by(func.coalesce(envelopes.c.sent_at, envelopes.c.created).desc())
                                       .limit(EXPORT_LIMIT + 1)).mappings()]
    ids = [r['id'] for r in rows]
    people, docs = {}, {}
    for chunk in range(0, len(ids), 900):
        part = ids[chunk:chunk + 900]
        for p in c.execute(select(recipients.c.envelope_id, recipients.c.name, recipients.c.email, recipients.c.role, recipients.c.status, recipients.c.routing_order)
                           .where(recipients.c.envelope_id.in_(part)).order_by(recipients.c.routing_order)).mappings():
            people.setdefault(p['envelope_id'], []).append(dict(p))
        for d in c.execute(select(esign_documents.c.envelope_id, esign_documents.c.name).where(esign_documents.c.envelope_id.in_(part), esign_documents.c.source != 'attachment')
                           .order_by(esign_documents.c.position)):
            docs.setdefault(d[0], []).append(d[1])
    owners, clients = _owners(c, [r['owner_id'] for r in rows]), _client_names(c, [r['client_id'] for r in rows])
    for r in rows:
        ps = [p for p in people.get(r['id'], []) if p['status'] not in esign.INACTIVE]
        signers = [p for p in ps if p['role'] in esign.SIGNING_ROLES] or [p for p in ps if p['role'] != 'cc']
        done = [p for p in signers if p['status'] in esign.DONE_STATUSES]
        waiting = [p['name'] or p['email'] for p in signers if p['status'] in esign.OPEN_STATUSES] if r['status'] == 'sent' else []
        yield {'ref': r['ref'], 'subject': r['subject'], 'company': get_entity(r['entity'])['short'], 'client': clients.get(r['client_id'], ''),
               'sender': owners.get(r['owner_id'], ''), 'status': STATUS_LABELS.get(r['status'], r['status']), 'created': r['created'], 'sent': r['sent_at'],
               'completed': r['completed_at'], 'days_to_complete': round((r['completed_at'] - r['sent_at']) / 86400, 1) if r['completed_at'] and r['sent_at'] else None,
               'recipients': ', '.join(f"{p['name'] or p['email']} ({ROLE_LABELS.get(p['role'], p['role']).lower()})" for p in ps),
               'progress': f'{len(done)} of {len(signers)}' if signers else '', 'waiting_on': ', '.join(waiting), 'documents': ', '.join(docs.get(r['id'], [])),
               'expires': r['expires_at'], 'last_change': r['updated'], '_link': {'envelope': r['id']}}


def rows_agreement_log(c, actor, f, tz):
    """The log of every agreement sent: company, client, which documents it carried, and its progress."""
    cond = [envelope_scope(actor, f['entity']), envelopes.c.status.notin_(('template', 'draft')),
            *_range(func.coalesce(envelopes.c.sent_at, envelopes.c.created), f), *_client_filter(envelopes.c.client_id, f)]
    if f['status']:
        cond.append(envelopes.c.status.in_(REPORT_STATUSES[f['status']]))
    if f['owner']:
        cond.append(envelopes.c.owner_id == f['owner'])
    rows = [dict(r) for r in c.execute(select(envelopes.c.id, envelopes.c.ref, envelopes.c.entity, envelopes.c.client_id, envelopes.c.owner_id,
                                              envelopes.c.status, envelopes.c.sent_at, envelopes.c.completed_at).where(*cond)
                                       .order_by(func.coalesce(envelopes.c.sent_at, envelopes.c.created).desc()).limit(EXPORT_LIMIT + 1)).mappings()]
    ids = [r['id'] for r in rows]
    agreements = {}
    for chunk in range(0, len(ids), 900):
        part = ids[chunk:chunk + 900]
        for d in c.execute(select(esign_documents.c.envelope_id, esign_documents.c.agreement).where(esign_documents.c.envelope_id.in_(part), esign_documents.c.agreement.isnot(None))):
            agreements.setdefault(d[0], set()).add(d[1])
    owners, clients = _owners(c, [r['owner_id'] for r in rows]), _client_names(c, [r['client_id'] for r in rows])
    for r in rows:
        kinds = agreements.get(r['id'], set())
        yield {'source': get_entity(r['entity'])['short'], 'client': clients.get(r['client_id'], ''),
               'baa': 'baa' in kinds, 'sa': 'services' in kinds, 'nda': 'nda' in kinds, 'pa': 'pa' in kinds,
               'electronic': r['sent_at'] is not None, 'executed': r['status'] == 'completed',
               'sent': r['sent_at'], 'sender': owners.get(r['owner_id'], ''), 'status': STATUS_LABELS.get(r['status'], r['status']),
               'completed': r['completed_at'], 'ref': r['ref'], '_link': {'envelope': r['id']}}


def rows_recipients(c, actor, f, tz):
    cond = [envelope_scope(actor, f['entity']), envelopes.c.status.notin_(('template', 'draft')), *_range(envelopes.c.sent_at, f), *_client_filter(envelopes.c.client_id, f)]
    if f['status']:
        cond.append(envelopes.c.status.in_(REPORT_STATUSES[f['status']]))
    if f['owner']:
        cond.append(envelopes.c.owner_id == f['owner'])
    rows = [dict(r) for r in c.execute(select(recipients, envelopes.c.ref, envelopes.c.subject, envelopes.c.entity, envelopes.c.client_id, envelopes.c.owner_id,
                                              envelopes.c.status.label('envelope_status'), envelopes.c.sent_at.label('envelope_sent'))
                                       .join(envelopes, envelopes.c.id == recipients.c.envelope_id).where(*cond)
                                       .order_by(envelopes.c.sent_at.desc(), recipients.c.routing_order).limit(EXPORT_LIMIT + 1)).mappings()]
    owners, clients = _owners(c, [r['owner_id'] for r in rows]), _client_names(c, [r['client_id'] for r in rows])
    for r in rows:
        yield {'ref': r['ref'], 'subject': r['subject'], 'company': get_entity(r['entity'])['short'], 'client': clients.get(r['client_id'], ''),
               'sender': owners.get(r['owner_id'], ''), 'envelope_status': STATUS_LABELS.get(r['envelope_status'], r['envelope_status']), 'name': r['name'],
               'email': r['email'], 'role': ROLE_LABELS.get(r['role'], r['role']), 'order': r['routing_order'], 'status': RECIPIENT_STATUS.get(r['status'], r['status']),
               'sent': r['sent_at'], 'viewed': r['delivered_at'], 'signed': r['signed_at'], 'declined_reason': r['decline_reason'] or '',
               'delivery': {'pending': 'Not sent yet', 'sent': 'Delivered to the mail server', 'bounced': 'Undeliverable', 'failed': 'Not sent'}.get(r['delivery'], r['delivery']),
               'reminders': r['reminders_sent'], 'ip': r['ip'] or '', 'authentication': r['auth_method'] or '', '_link': {'envelope': r['envelope_id']}}


def rows_agreements(c, actor, f, tz):
    cond = [sec.scope(actor, db.contracts.c.owner_id), sec.company_scope(actor, db.contracts.c.entity), *_range(db.contracts.c.created, f),
            *_client_filter(db.contracts.c.client_id, f)]
    if f['entity']:
        cond.append(db.contracts.c.entity == f['entity'])
    if f['owner']:
        cond.append(db.contracts.c.owner_id == f['owner'])
    rows = [dict(r) for r in c.execute(select(db.contracts.c.id, db.contracts.c.client_id, db.contracts.c.owner_id, db.contracts.c.entity, db.contracts.c.created,
                                              db.contracts.c.payload, db.contracts.c.legacy).where(*cond).order_by(db.contracts.c.created.desc())
                                       .limit(EXPORT_LIMIT + 1)).mappings()]
    ids = [r['id'] for r in rows]
    sent = {}
    for chunk in range(0, len(ids), 900):
        for e in c.execute(select(envelopes.c.contract_id, envelopes.c.status, envelopes.c.completed_at, envelopes.c.ref).where(
                envelopes.c.contract_id.in_(ids[chunk:chunk + 900]), envelopes.c.status != 'template').order_by(envelopes.c.created)).mappings():
            sent[e['contract_id']] = dict(e)
    owners, clients = _owners(c, [r['owner_id'] for r in rows]), _client_names(c, [r['client_id'] for r in rows])
    for r in rows:
        p = r['payload'] or {}
        e = sent.get(r['id'])
        services = [s.get('name') or s.get('title') or s.get('id') for s in (p.get('services') or []) if isinstance(s, dict)]
        services += [s.get('name') for s in (p.get('custom_services') or []) if isinstance(s, dict) and s.get('name')]
        signer = p.get('company_signatory') or {}
        yield {'contract': r['id'], 'client': clients.get(r['client_id'], ''), 'company': get_entity(r['entity'])['short'], 'created': r['created'],
               'created_by': 'Legacy import' if r['legacy'] else owners.get(r['owner_id'], ''),
               'documents': ', '.join(AGREEMENTS.get(x, x) for x in (p.get('document_types') or [])), 'effective_date': p.get('effective_date', ''),
               'governing_state': p.get('governing_state', ''), 'services': ', '.join(str(s) for s in services if s),
               'signatory': f"{signer.get('name', '')}, {signer.get('title', '')}" if signer else '',
               'esign': f"{STATUS_LABELS.get(e['status'], e['status'])} ({e['ref']})" if e else 'Not sent', 'signed': e['completed_at'] if e else None}


def rows_clients(c, actor, f, tz):
    cond = [sec.scope(actor, db.clients.c.owner_id), *_range(func.coalesce(db.clients.c.created, db.clients.c.updated), f)]
    if f['client']:
        cond.append(func.lower(db.clients.c.name).contains(f['client'].lower(), autoescape=True))
    if f['owner']:
        cond.append(db.clients.c.owner_id == f['owner'])
    rows = [dict(r) for r in c.execute(select(db.clients.c.id, db.clients.c.name, db.clients.c.address, db.clients.c.owner_id, db.clients.c.created,
                                              db.clients.c.updated, db.clients.c.profile).where(*cond).order_by(db.clients.c.name).limit(EXPORT_LIMIT + 1)).mappings()]
    ids = [r['id'] for r in rows]
    agreements, stats = {}, {}
    for chunk in range(0, len(ids), 900):
        part = ids[chunk:chunk + 900]
        contract_cond = [db.contracts.c.client_id.in_(part), sec.scope(actor, db.contracts.c.owner_id), sec.company_scope(actor, db.contracts.c.entity)]
        if f['entity']:
            contract_cond.append(db.contracts.c.entity == f['entity'])
        agreements.update(dict(c.execute(select(db.contracts.c.client_id, func.count()).where(*contract_cond).group_by(db.contracts.c.client_id)).all()))
        for e in c.execute(select(envelopes.c.client_id, envelopes.c.status, envelopes.c.sent_at).where(envelopes.c.client_id.in_(part), envelope_scope(actor, f['entity']),
                                                                                                        envelopes.c.status.notin_(('template', 'draft')))).mappings():
            s = stats.setdefault(e['client_id'], {'envelopes': 0, 'completed': 0, 'waiting': 0, 'last_sent': None})
            s['envelopes'] += 1
            s['completed'] += e['status'] == 'completed'
            s['waiting'] += e['status'] in ('sent', 'completing')
            s['last_sent'] = max(filter(None, (s['last_sent'], e['sent_at'])), default=None)
    owners = _owners(c, [r['owner_id'] for r in rows])
    for r in rows:
        p, s = r['profile'] or {}, stats.get(r['id'], {})
        yield {'client': r['name'], 'address': r['address'], 'phone': p.get('phone', ''), 'email': p.get('email', ''), 'owner': owners.get(r['owner_id'], ''),
               'created': r['created'], 'updated': r['updated'], 'agreements': agreements.get(r['id'], 0), 'envelopes': s.get('envelopes', 0),
               'completed': s.get('completed', 0), 'waiting': s.get('waiting', 0), 'last_sent': s.get('last_sent'), '_link': {'client': r['id']}}


def rows_activity(c, actor, f, tz):
    entries, _ = activity(c, actor, {'since': f['since'], 'until': f['until'], 'user': f['owner'], 'client': f['client'], 'entity': f['entity']}, 0, EXPORT_LIMIT + 1)
    for e in entries:
        yield {'at': e['at'], 'who': e['who'], 'kind': {'user': 'Portal user', 'signer': 'Signer', 'system': 'System'}[e['type']], 'action': e['action'],
               'detail': e['detail'], 'client': e['client'], 'ip': e['ip'], 'browser': e['browser']}


def rows_users(c, actor, f, tz):
    cond = [*_range(db.users.c.created, f)]
    members = team(actor)
    if members is not None:
        cond.append(db.users.c.id.in_(members))
    if f['owner']:
        cond.append(db.users.c.id == f['owner'])
    rows = [dict(r) for r in c.execute(select(db.users).where(*cond).order_by(db.users.c.name).limit(EXPORT_LIMIT + 1)).mappings()]
    ids = [r['id'] for r in rows]
    admins = _owners(c, [r['admin_id'] for r in rows])
    last = dict(c.execute(select(db.audit.c.actor_id, func.max(db.audit.c.created)).where(db.audit.c.actor_id.in_(ids or ['']),
                                                                                       db.audit.c.action.in_(('user.signed_in', 'user.sso_signed_in')))
                          .group_by(db.audit.c.actor_id)).all())
    contracts = dict(c.execute(select(db.contracts.c.owner_id, func.count()).where(db.contracts.c.owner_id.in_(ids or [''])).group_by(db.contracts.c.owner_id)).all())
    sent = dict(c.execute(select(envelopes.c.owner_id, func.count()).where(envelopes.c.owner_id.in_(ids or ['']), envelopes.c.sent_at.isnot(None))
                          .group_by(envelopes.c.owner_id)).all())
    done = dict(c.execute(select(envelopes.c.owner_id, func.count()).where(envelopes.c.owner_id.in_(ids or ['']), envelopes.c.status == 'completed')
                          .group_by(envelopes.c.owner_id)).all())
    for r in rows:
        yield {'name': r['name'], 'email': r['email'], 'role': r['role'].replace('_', ' ').title(),
               'companies': 'All companies' if r['role'] == 'super_admin' else ', '.join(get_entity(x)['short'] for x in (r['companies'] or []) if x in ENTITIES),
               'admin': admins.get(r['admin_id'], ''), 'status': 'Active' if r['active'] else 'Disabled', 'created': r['created'],
               'last_sign_in': last.get(r['id']), 'agreements': contracts.get(r['id'], 0), 'envelopes': sent.get(r['id'], 0), 'completed': done.get(r['id'], 0)}


BUILDERS = {'agreement_log': rows_agreement_log, 'envelopes': rows_envelopes, 'recipients': rows_recipients, 'agreements': rows_agreements,
            'clients': rows_clients, 'activity': rows_activity, 'users': rows_users}


def run_report(c, actor: dict, payload: dict, limit: int):
    name = payload.get('dataset')
    if name not in DATASETS:
        raise ValueError('Choose a report.')
    spec = DATASETS[name]
    if spec['admin']:
        admin_only(actor)
    known = {k: (label, kind) for k, label, kind in spec['columns']}
    columns = [k for k in (payload.get('columns') or spec['default']) if k in known] or spec['default']
    f = filters_of(payload)
    rows = []
    for row in BUILDERS[name](c, actor, f, tz_of(f['tz'])):
        rows.append(row)
        if len(rows) > limit:
            break
    return name, spec, [(k, *known[k]) for k in columns], rows[:limit], len(rows) > limit, f


def screen_value(value, kind, tz):
    if kind == 'datetime':
        return text_time(value, tz)
    if kind == 'bool':
        return '✓' if value else ''
    return '' if value is None else value


def safe_text(value) -> str:
    """Spreadsheet cells never start a formula with text that came from data."""
    text = str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


def export_report(name, spec, columns, rows, f, actor, fmt) -> tuple[bytes, str, str]:
    tz = tz_of(f['tz'])
    stamp = dt.datetime.now(tz).strftime('%Y-%m-%d')
    filename = f"TFSC {spec['label']} report {stamp}"
    if fmt == 'csv':
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([label for _, label, _ in columns])
        for row in rows:
            writer.writerow([text_time(row.get(k), tz) if kind == 'datetime' else ('Yes' if row.get(k) else '') if kind == 'bool' else
                             safe_text(row.get(k, '')) if isinstance(row.get(k), str) else
                             ('' if row.get(k) is None else row.get(k)) for k, _, kind in columns])
        return ('﻿' + out.getvalue()).encode('utf-8'), 'text/csv; charset=utf-8', filename + '.csv'
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = spec['label'][:31]
    header_fill, header_font = PatternFill('solid', fgColor='0B2545'), Font(bold=True, color='FFFFFF')
    ws.append([label for _, label, _ in columns])
    for cell in ws[1]:
        cell.fill, cell.font = header_fill, header_font
        cell.alignment = Alignment(vertical='center')
    widths = [len(label) + 2 for _, label, _ in columns]
    for row in rows:
        values = []
        for i, (k, _, kind) in enumerate(columns):
            v = row.get(k)
            if kind == 'datetime':
                v = as_time(v, tz)
            elif kind == 'bool':
                v = 'Yes' if v else ''
            elif isinstance(v, str):
                v = v[:32000]
            values.append(v)
            widths[i] = min(60, max(widths[i], 17 if kind == 'datetime' else len(str(v or '')) + 2))
        ws.append(values)
        for i, (k, _, kind) in enumerate(columns):
            cell = ws.cell(row=ws.max_row, column=i + 1)
            if kind == 'datetime' and cell.value:
                cell.number_format = 'yyyy-mm-dd hh:mm'
            elif isinstance(cell.value, str) and cell.value.startswith('='):
                cell.data_type = 's'      # text from data, never a formula
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'
    if rows:
        ws.auto_filter.ref = f'A1:{get_column_letter(len(columns))}{len(rows) + 1}'
    info = wb.create_sheet('Report details')
    for line in [('Report', spec['label']), ('Prepared by', f"{actor['name']} ({actor['email']})"), ('Prepared', dt.datetime.now(tz).strftime('%Y-%m-%d %H:%M %Z')),
                 ('Date range', f"{text_time(f['since'], tz) or 'Any'} to {text_time(f['until'], tz) or 'now'} ({spec['date']})"),
                 ('Company', get_entity(f['entity'])['name'] if f['entity'] else 'All permitted companies'), ('Status', f['status'] or 'Any'),
                 ('Client contains', f['client'] or 'Any'), ('Rows', len(rows)), ('Confidential', 'Contains client and signer details. Store and share it securely.')]:
        info.append(list(line))
    info.column_dimensions['A'].width, info.column_dimensions['B'].width = 18, 80
    for cell in info['A']:
        cell.font = Font(bold=True)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename + '.xlsx'


# ------------------------------------------------------------------ activity log
def activity(c, actor: dict, f: dict, page: int, limit: int = 50, client_id: str | None = None) -> tuple[list[dict], bool]:
    """The merged activity log, newest first. f: since, until, user (an account id, or 'signers'), client (name contains),
    category, q, entity. client_id limits it to one client (the client profile)."""
    want = (page + 1) * limit + 1
    since, until = f.get('since'), f.get('until')
    members = team(actor)
    entries = []
    user = f.get('user') or ''
    category = f.get('category') or ''
    q = (f.get('q') or '').strip().lower()[:100]
    client_like = (f.get('client') or '').strip().lower()
    if user != 'signers':
        cond = [db.audit.c.action.notin_(MIRRORED)]
        if members is not None:
            visible_clients = select(db.clients.c.id).where(sec.scope(actor, db.clients.c.owner_id))
            cond.append(or_(db.audit.c.actor_id.in_(members), and_(db.audit.c.actor_id.is_(None), db.audit.c.client_id.in_(visible_clients))))
        if client_id:
            cond.append(db.audit.c.client_id == client_id)
        if user:
            cond.append(db.audit.c.actor_id == user)
        if since:
            cond.append(db.audit.c.created >= since)
        if until:
            cond.append(db.audit.c.created < until)
        if category in CATEGORIES:
            cond.append(or_(*[db.audit.c.action.startswith(p) if p.endswith('.') else db.audit.c.action == p for p in CATEGORIES[category]]))
        if client_like:
            cond.append(db.audit.c.client_id.in_(select(db.clients.c.id).where(func.lower(db.clients.c.name).contains(client_like, autoescape=True))))
        if q:
            cond.append(or_(func.lower(db.audit.c.actor_name).contains(q, autoescape=True), func.lower(db.audit.c.action).contains(q, autoescape=True)))
        for r in c.execute(select(db.audit).where(*cond).order_by(db.audit.c.created.desc(), db.audit.c.id).limit(want)).mappings():
            detail = r['detail'] if isinstance(r['detail'], dict) else {}
            if f.get('entity') and detail.get('entity') not in (None, f['entity']):
                continue
            who = r['actor_name']
            if not r['actor_id'] and who == 'Legacy import' and not r['action'].startswith('legacy.'):
                who = 'TFSC Agreements'          # before 5.0, entries without an account were labelled this way
            entries.append({'at': r['created'], 'who': who, 'user_id': r['actor_id'], 'type': 'user' if r['actor_id'] else 'system',
                            'code': r['action'], 'action': ACTIONS.get(r['action'], r['action'].replace('.', ' ').replace('_', ' ').capitalize()),
                            'detail': describe(r['action'], detail), 'client_id': r['client_id'], 'ip': r['ip'] or '', 'browser': browser(r['user_agent']),
                            'envelope_id': None})
    if not user or user == 'signers':
        if category in ('', 'envelopes'):
            cond = [esign.visible(actor), events.c.kind.in_(SIGNER_EVENTS)]
            if f.get('entity'):
                cond.append(envelopes.c.entity == f['entity'])
            if client_id:
                cond.append(envelopes.c.client_id == client_id)
            if since:
                cond.append(events.c.at >= since)
            if until:
                cond.append(events.c.at < until)
            if client_like:
                cond.append(envelopes.c.client_id.in_(select(db.clients.c.id).where(func.lower(db.clients.c.name).contains(client_like, autoescape=True))))
            if q:
                cond.append(or_(func.lower(events.c.actor).contains(q, autoescape=True), func.lower(events.c.kind).contains(q, autoescape=True),
                                func.lower(envelopes.c.subject).contains(q, autoescape=True)))
            names = {}
            rows = c.execute(select(events.c.at, events.c.kind, events.c.actor, events.c.ip, events.c.user_agent, events.c.detail, events.c.recipient_id,
                                    envelopes.c.id.label('envelope_id'), envelopes.c.ref, envelopes.c.subject, envelopes.c.client_id)
                             .join(envelopes, envelopes.c.id == events.c.envelope_id).where(*cond).order_by(events.c.at.desc()).limit(want)).mappings().all()
            ids = {r['recipient_id'] for r in rows if r['recipient_id']}
            if ids:
                names = {r[0]: r[1] for r in c.execute(select(recipients.c.id, recipients.c.email).where(recipients.c.id.in_(ids)))}
            for r in rows:
                detail = r['detail'] if isinstance(r['detail'], dict) else {}
                email = names.get(r['recipient_id'], '')
                system = r['kind'] in ('bounced', 'replied') or r['actor'] == 'TFSC Agreements'
                entries.append({'at': r['at'], 'who': r['actor'] + (f' ({email})' if email and not system else ''), 'user_id': None, 'type': 'system' if system else 'signer',
                                'code': 'signer.' + r['kind'], 'action': esign.LABELS.get(r['kind'], r['kind']),
                                'detail': ' · '.join(x for x in (f"{r['subject']} ({r['ref']})", str(detail.get('document') or detail.get('name') or detail.get('reason') or '')) if x)[:400],
                                'client_id': r['client_id'], 'ip': r['ip'] or '', 'browser': browser(r['user_agent']), 'envelope_id': r['envelope_id']})
    entries.sort(key=lambda e: e['at'], reverse=True)
    window = entries[page * limit:(page + 1) * limit]
    names = _client_names(c, [e['client_id'] for e in window])
    for e in window:
        e['client'] = names.get(e['client_id'], '')
    return window, len(entries) > (page + 1) * limit


def activity_filters(payload: dict) -> dict:
    out = {}
    for key in ('since', 'until'):
        try:
            out[key] = float(payload[key]) if payload.get(key) not in (None, '') else None
        except (TypeError, ValueError):
            raise ValueError('Invalid date range.') from None
    for key, limit in (('user', 32), ('client', 200), ('category', 20), ('q', 100), ('entity', 100), ('tz', 60)):
        out[key] = str(payload.get(key) or '')[:limit]
    return out


# ------------------------------------------------------------------ routes
def install(app) -> None:  # noqa: C901  (route table)
    def actor_of(request, c):
        return sec.get_actor(request, c)

    @app.get('/api/home')
    def api_home(request: Request, entity: str = ''):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            if entity:
                sec.ensure_company(a, entity)
            return home(c, a, entity)

    @app.get('/api/insights')
    def api_insights(request: Request, entity: str = '', months: int = 6, tz: str = 'UTC'):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            if entity:
                sec.ensure_company(a, entity)
            return insights(c, a, entity, months, tz)

    @app.get('/api/reports/catalog')
    def api_catalog(request: Request):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            members = team(a)
            q = select(db.users.c.id, db.users.c.name, db.users.c.email).order_by(db.users.c.name).limit(500)
            users = [dict(r) for r in c.execute(q.where(db.users.c.id.in_(members)) if members is not None else q).mappings()]
            saved = [dict(r) for r in c.execute(select(db.saved_reports.c.id, db.saved_reports.c.name, db.saved_reports.c.definition, db.saved_reports.c.updated)
                                                .where(db.saved_reports.c.owner_id == a['id']).order_by(db.saved_reports.c.name)).mappings()]
        return {'datasets': [{'key': k, 'label': v['label'], 'date': v['date'], 'columns': [{'key': x[0], 'label': x[1], 'type': x[2]} for x in v['columns']],
                              'default': v['default']} for k, v in DATASETS.items() if not v['admin'] or a['role'] != 'user'],
                'users': users, 'statuses': [{'key': k, 'label': {'waiting': 'Waiting for others'}.get(k, k.capitalize())} for k in REPORT_STATUSES],
                'companies': [{'name': n, 'short': e['short']} for n, e in ENTITIES.items() if a['role'] == 'super_admin' or n in a['companies']], 'saved': saved}

    @app.post('/api/reports/run')
    def api_run(request: Request, payload: dict = Body(...)):
        page = max(0, min(int(payload.get('page') or 0), 1000))
        with db.engine.connect() as c:
            a = actor_of(request, c)
            name, spec, columns, rows, more, f = run_report(c, a, payload, SCREEN_LIMIT)
        tz = tz_of(f['tz'])
        window = rows[page * 100:(page + 1) * 100]
        return {'dataset': name, 'columns': [{'key': k, 'label': label, 'type': kind} for k, label, kind in columns],
                'rows': [[screen_value(r.get(k), kind, tz) for k, _, kind in columns] for r in window], 'links': [r.get('_link') for r in window],
                'total': len(rows), 'capped': more, 'page': page, 'has_more': (page + 1) * 100 < len(rows)}

    @app.post('/api/reports/export')
    def api_export(request: Request, payload: dict = Body(...)):
        fmt = 'csv' if payload.get('format') == 'csv' else 'xlsx'
        with db.engine.begin() as c:
            a = actor_of(request, c)
            name, spec, columns, rows, more, f = run_report(c, a, payload, EXPORT_LIMIT)
            db.log(c, a, 'report.exported', detail={'report': spec['label'], 'format': fmt, 'rows': len(rows), 'columns': [k for k, _, _ in columns]})
        body, media, filename = export_report(name, spec, columns, rows, f, a, fmt)
        return Response(body, media_type=media, headers={'Content-Disposition': f'attachment; filename="{filename}"'})

    @app.post('/api/reports/saved')
    def api_save_report(request: Request, payload: dict = Body(...)):
        with db.engine.begin() as c:
            a = actor_of(request, c)
            name = esign.clean(payload.get('name'), 'Report name', 120, True)
            definition = payload.get('definition')
            if not isinstance(definition, dict) or definition.get('dataset') not in DATASETS:
                raise ValueError('Choose a report to save.')
            if DATASETS[definition['dataset']]['admin']:
                admin_only(a)
            definition = {'dataset': definition['dataset'], 'columns': [str(x)[:40] for x in (definition.get('columns') or [])][:40],
                          'filters': {k: v for k, v in (definition.get('filters') or {}).items() if k in ('entity', 'status', 'owner', 'client', 'range')}}
            t = time.time()
            existing = c.execute(select(db.saved_reports.c.id).where(db.saved_reports.c.owner_id == a['id'], db.saved_reports.c.name == name)).scalar()
            if existing:
                c.execute(db.saved_reports.update().where(db.saved_reports.c.id == existing).values(definition=definition, updated=t))
            else:
                c.execute(db.saved_reports.insert().values(id=db.uid(), owner_id=a['id'], name=name, definition=definition, created=t, updated=t))
            return {'ok': True}

    @app.post('/api/reports/saved/{report_id}/delete')
    def api_delete_report(report_id: str, request: Request):
        with db.engine.begin() as c:
            a = actor_of(request, c)
            if c.execute(db.saved_reports.delete().where(db.saved_reports.c.id == report_id, db.saved_reports.c.owner_id == a['id'])).rowcount != 1:
                raise HTTPException(404, 'Report not found.')
            return {'ok': True}

    @app.get('/api/activity')
    def api_activity(request: Request, page: int = 0, since: str = '', until: str = '', user: str = '', client: str = '', category: str = '', q: str = '',
                     entity: str = '', tz: str = 'UTC'):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            admin_only(a)
            f = activity_filters({'since': since, 'until': until, 'user': user, 'client': client, 'category': category, 'q': q, 'entity': entity})
            rows, more = activity(c, a, f, max(0, min(page, 2000)))
        return {'entries': rows, 'has_more': more, 'page': page}

    @app.post('/api/activity/export')
    def api_activity_export(request: Request, payload: dict = Body(...)):
        fmt = 'csv' if payload.get('format') == 'csv' else 'xlsx'
        with db.engine.begin() as c:
            a = actor_of(request, c)
            admin_only(a)
            f = activity_filters(payload.get('filters') or {})
            rows, _ = activity(c, a, f, 0, EXPORT_LIMIT)
            db.log(c, a, 'activity.exported', detail={'format': fmt, 'rows': len(rows)})
        spec = DATASETS['activity']
        columns = [(k, label, kind) for k, label, kind in spec['columns']]
        out_rows = [{'at': e['at'], 'who': e['who'], 'kind': {'user': 'Portal user', 'signer': 'Signer', 'system': 'System'}[e['type']], 'action': e['action'],
                     'detail': e['detail'], 'client': e['client'], 'ip': e['ip'], 'browser': e['browser']} for e in rows]
        rf = {'since': f['since'], 'until': f['until'], 'entity': f['entity'] if f['entity'] in ENTITIES else '', 'status': '', 'client': f['client'], 'tz': f['tz'] or 'UTC'}
        body, media, filename = export_report('activity', dict(spec, label='Activity log'), columns, out_rows, rf, a, fmt)
        return Response(body, media_type=media, headers={'Content-Disposition': f'attachment; filename="{filename}"'})

    @app.get('/api/clients/{client_id}/activity')
    def api_client_activity(client_id: str, request: Request, page: int = 0):
        import clients
        with db.engine.connect() as c:
            a = actor_of(request, c)
            clients.client_for(c, a, client_id)
            rows, more = activity(c, a, {}, max(0, min(page, 2000)), 50, client_id=client_id)
        return {'activity': rows, 'has_more': more}

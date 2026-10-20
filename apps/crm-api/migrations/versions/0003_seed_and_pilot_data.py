"""Seed v2 catalogs (§9) and copy pilot v1.0.1 data into the v2 tables (§25.2).

Frozen data migration: it does not import application code. It is idempotent per
org (skips when the org already has v2 users) and keeps every pilot table intact,
so the pilot can still be inspected or restored until the cutover drops them.

Pilot -> v2 mapping
- crm_users -> users (same UUID; legacy_id kept), photo -> stored_files
- agreements_user_id -> module_grants(module='agreements', role='user')
- crm_teams/crm_team_members -> teams(kind='group') + team_memberships (effective-dated)
- crm_activities -> activities (raw kept; phone/email blind-indexed and field-encrypted)
- crm_leads by stage: follow_up -> followups; strong_* -> strong_followups;
  qualified_lead/meeting/signed_up/lost -> leads (+ lead_ownership_claims from crm_ownership_claims)
- crm_followup_tasks -> followups (delegated task due date/notes)
- crm_conflicts -> flags; crm_meetings -> meetings; crm_target_versions -> targets;
  crm_settings.policy -> settings; crm_audit -> audit_log (hash chain anchored at migration)
- phone_registry rebuilt from the copied activities/leads.
score_ledger and rollups are rebuilt by migration 0004 (P5).

Revision ID: 0003_seed_and_pilot_data
Revises: 0002_v2_foundation
Create Date: 2026-10-20
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import UTC, date, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP, UUID

revision = '0003_seed_and_pilot_data'
down_revision = '0002_v2_foundation'
branch_labels = None
depends_on = None

DOMAINS = [
    ('scribe_align', 'Scribe Align LLC', 'SCRIBE ALIGN LLC'),
    ('extended_health', 'Extended Health Services Inc.', 'EXTENDED HEALTH SERVICES INC.'),
    ('mb_consultancy', 'MB Consultancy LLC', None),
    ('24_7_consultancy', '24-7 Consultancy LLC', '24-7 CONSULTANCY LLC'),
    ('wisemed', 'Wisemed Solutions', None),
    ('clinic_heroes', 'Clinic Heroes', None),
    ('quantum_medical', 'Quantum Medical Services', None),
    ('mrx', 'MRX', None),
    ('house_of_rcm', 'House of RCM', None),
]
PROJECTS = ['P0000', 'P0001', 'P0004', 'P0005', 'P0006', 'P0007', 'P0008', 'P0009', 'P0010', 'P0011', 'P0012',
            'P0013', 'P0014', 'P0016', 'P0017', 'P0018', 'P0020', 'P0021', 'P0022', 'P0023', 'P0024', 'P0025',
            'P0026', 'P0027', 'P0028', 'P0029', 'P0030', 'P0031']
DEFAULT_SETTINGS = {
    'lock_windows': {'manual_minutes': 60, 'import_minutes': 15, 'import_min': 10, 'import_max': 20},
    'celebration': {'template': '🎉 Congratulations to {agent} for a successful sale! Thank you, {supervisor} and management.', 'seconds': 6},
    'names': {'suffix_comma': True},
    'dialer': {'mode': 'tel', 'template': None},
    'lead_protection_days': 90,
    'auto_assignment': {'round_robin': False},
    'agent_meeting_label': 'With management',
    'anti_scraping': {'prospects_per_hour': 500},
}
BANDS = {'extraordinary': 0.80, 'satisfactory': 0.65, 'average': 0.50, 'below_average': 0.35}
_DEV_SECRET = 'local-development-phone-key-not-for-production'  # noqa: S105 - dev only; hosted mode requires CRM_PHONE_KEY_SECRET



def _uuid7() -> uuid.UUID:
    ts = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), 'big')
    v = (ts & 0xFFFFFFFFFFFF) << 80 | 0x7 << 76 | (rand >> 68 & 0xFFF) << 64 | 0b10 << 62 | (rand & 0x3FFFFFFFFFFFFFFF)
    return uuid.UUID(int=v)


def _bi(value: str | None, kind: str) -> str | None:
    if not value:
        return None
    secret = (os.getenv('CRM_PHONE_KEY_SECRET') or _DEV_SECRET).encode()
    return hmac.new(secret, f'{kind}:{value}'.encode(), hashlib.sha256).hexdigest()


def _enc(plain: str | None) -> str | None:
    if not plain:
        return plain
    key = os.getenv('CRM_FIELD_KEY')
    if not key:
        return 'p1:' + plain
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return 'v1:' + base64.b64encode(nonce + AESGCM(base64.b64decode(key)).encrypt(nonce, plain.encode(), None)).decode()


def _ts(epoch: float | None) -> datetime | None:
    return datetime.fromtimestamp(epoch, UTC) if epoch else None


def _uid(hex32: str | None) -> uuid.UUID | None:
    return uuid.UUID(hex32) if hex32 else None


def _iso(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        if len(v) == 10:
            return datetime.combine(date.fromisoformat(v), datetime.min.time()).replace(tzinfo=UTC)
        d = datetime.fromisoformat(v.replace('Z', '+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return None


def _audit_hash(prev: str, row: dict) -> str:
    body = json.dumps({k: (str(v) if v is not None else None) for k, v in sorted(row.items())}, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256((prev + body).encode()).hexdigest()


def upgrade() -> None:
    op.create_table(
        'stored_files',
        sa.Column('key', sa.String(300), primary_key=True),
        sa.Column('org_id', UUID(as_uuid=True), sa.ForeignKey('orgs.id'), nullable=False),
        sa.Column('content_type', sa.String(100), nullable=False),
        sa.Column('size', sa.Integer, nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('data', BYTEA, nullable=False),
        sa.Column('created_at', TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute('ALTER TABLE stored_files ENABLE ROW LEVEL SECURITY')
    op.execute("CREATE POLICY org_isolation ON stored_files USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid) "
               "WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)")
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON stored_files TO crm_app')

    c = op.get_bind()
    md = sa.MetaData()
    T = {n: sa.Table(n, md, autoload_with=c) for n in [
        'orgs', 'domains', 'projects', 'users', 'module_grants', 'teams', 'team_memberships', 'activities',
        'followups', 'strong_followups', 'leads', 'lead_ownership_claims', 'flags', 'meetings', 'targets', 'settings',
        'audit_log', 'phone_registry', 'stored_files', 'user_domain_assignments', 'attribution_ledger']}
    P = {n: sa.Table(n, md, autoload_with=c) for n in [
        'crm_users', 'crm_teams', 'crm_team_members', 'crm_activities', 'crm_leads', 'crm_ownership_claims',
        'crm_conflicts', 'crm_meetings', 'crm_audit', 'crm_settings', 'crm_target_versions', 'crm_followup_tasks',
        'crm_projects', 'crm_domains']}

    pilot_orgs = [r[0] for r in c.execute(sa.select(P['crm_users'].c.org_id).distinct())]
    org_ids: list[uuid.UUID] = [uuid.UUID(o) for o in pilot_orgs]
    if not org_ids and not c.execute(sa.select(T['orgs'].c.id).limit(1)).first():
        org_ids = [_uuid7()]
    for org_id in org_ids:
        if not c.execute(sa.select(T['orgs'].c.id).where(T['orgs'].c.id == org_id)).first():
            c.execute(T['orgs'].insert().values(id=org_id, name=os.getenv('CRM_ORG_NAME', 'TFSC')))
        _seed(c, T, org_id)
        if c.execute(sa.select(T['users'].c.id).where(T['users'].c.org_id == org_id).limit(1)).first():
            continue
        _copy_pilot(c, T, P, org_id)


def _seed(c, T, org_id) -> None:
    for i, (did, name, entity) in enumerate(DOMAINS):
        if not c.execute(sa.select(T['domains'].c.id).where(T['domains'].c.id == did)).first():
            c.execute(T['domains'].insert().values(id=did, org_id=org_id, name=name, legal_name=name, tfsc_entity_key=entity, sort=i))
    for pid in PROJECTS:
        if not c.execute(sa.select(T['projects'].c.id).where(T['projects'].c.id == pid)).first():
            c.execute(T['projects'].insert().values(id=pid, org_id=org_id, name=pid, status='pending_configuration'))
    if not c.execute(sa.select(T['targets'].c.id).where(T['targets'].c.org_id == org_id).limit(1)).first():
        c.execute(T['targets'].insert().values(id=_uuid7(), org_id=org_id, effective_date=date(2026, 1, 1), call_target=170, conn_target=0.6, bands=BANDS))
    for k, v in DEFAULT_SETTINGS.items():
        if not c.execute(sa.select(T['settings'].c.key).where(T['settings'].c.org_id == org_id, T['settings'].c.key == k)).first():
            c.execute(T['settings'].insert().values(org_id=org_id, key=k, value=v))


def _copy_pilot(c, T, P, org_id) -> None:
    org_hex = org_id.hex
    pu = P['crm_users']
    users = [dict(r) for r in c.execute(sa.select(pu).where(pu.c.org_id == org_hex)).mappings()]
    if not users:
        return
    known_domains = {r[0] for r in c.execute(sa.select(T['domains'].c.id))}
    for d in c.execute(sa.select(P['crm_domains'])).mappings():
        if d['id'] not in known_domains:
            c.execute(T['domains'].insert().values(id=d['id'], org_id=org_id, name=d['name'], active=d['active'], sort=99))
            known_domains.add(d['id'])
    for p in c.execute(sa.select(P['crm_projects'])).mappings():
        exists = c.execute(sa.select(T['projects'].c.id).where(T['projects'].c.id == p['id'])).first()
        vals = {'name': p['name'] or p['id'], 'domain_id': p['domain_id'], 'owner_name': p['owner_name'], 'owner_email': p['owner_email'],
                'status': 'active' if (p['active'] and p['owner_name']) else ('pending_configuration' if p['active'] else 'closed')}
        if exists:
            c.execute(T['projects'].update().where(T['projects'].c.id == p['id']).values(**vals))
        elif len(p['id']) == 5 and p['id'].startswith('P') and p['id'][1:].isdigit():
            c.execute(T['projects'].insert().values(id=p['id'], org_id=org_id, **vals))
    # users: insert without manager first, then link managers (self-referencing FK)
    for u in users:
        uid = _uid(u['id'])
        c.execute(T['users'].insert().values(
            id=uid, org_id=org_id, email=u['email'].lower(), display_name=u['name'], employee_code=u['agent_id'],
            status='active' if u['active'] else 'suspended', crm_role=u['role'], routing_view=u['routing_view'],
            default_domain_id=u['domain_id'] if u['domain_id'] in known_domains else 'scribe_align', default_project_id=u['project_id'],
            password_hash=u['password'], reset_required=u['reset_required'], legacy_id=u['id'],
            mfa_required=u['role'] in ('supervisor', 'manager', 'director_admin', 'super_admin'),
            created_at=_ts(u['created']), updated_at=_ts(u['created']), version=u['version']))
        c.execute(T['user_domain_assignments'].insert().values(
            id=_uuid7(), org_id=org_id, user_id=uid, domain_id=u['domain_id'] if u['domain_id'] in known_domains else 'scribe_align',
            project_id=u['project_id'], effective_from=_ts(u['created']).date()))
        if u['photo']:
            data = base64.b64decode(u['photo'])
            key = f'photos/{uid}.jpg'
            c.execute(T['stored_files'].insert().values(key=key, org_id=org_id, content_type='image/jpeg', size=len(data), sha256=hashlib.sha256(data).hexdigest(), data=data))
            c.execute(T['users'].update().where(T['users'].c.id == uid).values(photo_object_key=key))
        if u['agreements_user_id']:
            c.execute(T['module_grants'].insert().values(id=_uuid7(), org_id=org_id, user_id=uid, module='agreements', role='user',
                                                         external_account_id=u['agreements_user_id'], granted_at=_ts(u['created'])))
    for u in users:
        if u['manager_id']:
            c.execute(T['users'].update().where(T['users'].c.id == _uid(u['id'])).values(manager_id=_uid(u['manager_id'])))
    # teams -> groups
    for t in c.execute(sa.select(P['crm_teams']).where(P['crm_teams'].c.org_id == org_hex)).mappings():
        tid = _uid(t['id'])
        c.execute(T['teams'].insert().values(id=tid, org_id=org_id, name=t['name'], kind='group', supervisor_id=_uid(t['supervisor_id']),
                                             leader_id=_uid(t['leader_id']), legacy_id=t['id'], created_at=_ts(t['created'])))
        for m in c.execute(sa.select(P['crm_team_members']).where(P['crm_team_members'].c.team_id == t['id'])).mappings():
            c.execute(T['team_memberships'].insert().values(id=_uuid7(), org_id=org_id, team_id=tid, user_id=_uid(m['user_id']), effective_from=_ts(m['created']).date()))
    # activities
    act_dates: dict[str, date] = {}
    pa = P['crm_activities']
    seen_months: set[date] = set()
    for a in c.execute(sa.select(pa).where(pa.c.org_id == org_hex).order_by(pa.c.created)).mappings():
        bd = date.fromisoformat(a['business_date'])
        month = bd.replace(day=1)
        if month not in seen_months:
            c.execute(sa.text('SELECT crm_ensure_partition_for(:p, :d)'), {'p': 'activities', 'd': bd})
            c.execute(sa.text('SELECT crm_ensure_partition_for(:p, :d)'), {'p': 'score_ledger', 'd': bd})
            seen_months.add(month)
        act_dates[a['id']] = bd
        email = (a['email'] or '').strip() or None
        raw = dict(a['raw'] or {})
        raw.setdefault('_pilot', {'phone': a['phone'], 'email': a['email'], 'address': a['address'], 'source': a['source']})
        window = None
        if a['first_committed_at'] and a['edit_deadline']:
            window = round((a['edit_deadline'] - a['first_committed_at']) / 60)
        c.execute(T['activities'].insert().values(
            id=_uid(a['id']), org_id=org_id, owner_id=_uid(a['owner_id']), business_date=bd,
            entry_source={'manual': 'manual', 'import': 'import', 'paste': 'grid'}.get(a['source'], a['source'][:20]),
            raw=raw, provider_name=a['provider_name'], phone_display=_enc(a['phone']), phone_key=_bi(a['phone_key'], 'phone'),
            extension=a['extension'], email=_enc(email), email_key=_bi(email.lower() if email else None, 'email'),
            called=a['called'], connected=a['connected'], text_sent=a['text_sent'], email_sent=a['email_sent'],
            followup_needed=True if a['next_followup'] else None, next_followup_at=_iso(a['next_followup']), remarks=a['remarks'] or '',
            first_committed_at=_ts(a['first_committed_at']), edit_deadline=_ts(a['edit_deadline']), lock_window_minutes=window,
            deleted_at=_ts(a['updated']) if a['deleted'] else None, delete_reason='Deleted in pilot' if a['deleted'] else None,
            domain_id=a['domain_id'], project_id=a['project_id'], legacy_id=a['id'],
            created_at=_ts(a['created']), updated_at=_ts(a['updated']), version=a['version']))
    # leads by stage
    pl = P['crm_leads']
    tasks = {r['lead_id']: dict(r) for r in c.execute(sa.select(P['crm_followup_tasks'])).mappings()}
    claims = {r['lead_id']: dict(r) for r in c.execute(sa.select(P['crm_ownership_claims'])).mappings()}
    for ld in c.execute(sa.select(pl, pa.c.phone_key, pa.c.next_followup).join(pa, pa.c.id == pl.c.activity_id).where(pl.c.org_id == org_hex)).mappings():
        stage = ld['stage']
        bd = act_dates.get(ld['activity_id'])
        pk = _bi(ld['phone_key'], 'phone')
        task = tasks.get(ld['id'])
        if stage == 'follow_up':
            due = _iso(task['due_date']) if task else _iso(ld['next_followup'])
            c.execute(T['followups'].insert().values(id=_uid(ld['id']), org_id=org_id, activity_id=_uid(ld['activity_id']), activity_date=bd,
                                                     phone_key=pk, owner_id=_uid(ld['owner_id']), due_at=due, status='open',
                                                     outcome_note=task['notes'] if task else None, created_at=_ts(ld['created']), updated_at=_ts(ld['updated'])))
        elif stage in ('strong_follow_up_pending', 'strong_follow_up_confirmed'):
            c.execute(T['strong_followups'].insert().values(
                id=_uid(ld['id']), org_id=org_id, activity_id=_uid(ld['activity_id']), activity_date=bd, phone_key=pk,
                requested_by=_uid(ld['owner_id']), requested_at=_ts(ld['created']),
                decision='confirmed' if stage.endswith('confirmed') else 'pending',
                decided_by=_uid(ld['qualified_by']) if stage.endswith('confirmed') else None,
                decided_at=_ts(ld['updated']) if stage.endswith('confirmed') else None,
                created_at=_ts(ld['created']), updated_at=_ts(ld['updated'])))
        else:
            v2stage = {'qualified_lead': 'qualified_lead', 'meeting': 'meeting', 'signed_up': 'signed_up', 'lost': 'lost'}.get(stage, 'qualified_lead')
            c.execute(T['leads'].insert().values(
                id=_uid(ld['id']), org_id=org_id, origin_activity_id=_uid(ld['activity_id']), origin_activity_date=bd, phone_key=pk,
                owner_id=_uid(ld['owner_id']), stage=v2stage, stage_changed_at=_ts(ld['updated']), qualified_by=_uid(ld['qualified_by']),
                qualified_at=_ts(ld['created']), signed_at=_ts(ld['sale_confirmed_at']), legacy_id=ld['id'],
                created_at=_ts(ld['created']), updated_at=_ts(ld['updated']), version=ld['version']))
            cl = claims.get(ld['id'])
            if cl and pk and v2stage != 'lost':
                c.execute(T['lead_ownership_claims'].insert().values(org_id=org_id, phone_key=pk, lead_id=_uid(ld['id']), owner_id=_uid(cl['owner_id']), claimed_at=_ts(cl['created'])))
    # conflicts -> flags
    for f in c.execute(sa.select(P['crm_conflicts']).where(P['crm_conflicts'].c.org_id == org_hex)).mappings():
        sev = 'red' if 'lead' in f['severity'] else ('amber' if 'follow' in f['severity'] else 'info')
        c.execute(T['flags'].insert().values(
            id=_uid(f['id']), org_id=org_id, severity=sev, kind='duplicate_' + ('lead' if sev == 'red' else 'followup' if sev == 'amber' else 'seen'),
            activity_id=_uid(f['activity_id']), other_activity_id=_uid(f['other_id']), status='resolved' if f['status'] == 'resolved' else 'open',
            resolution_note=f['reason'], resolved_by=_uid(f['resolved_by']), detail={'pilot_severity': f['severity']}, created_at=_ts(f['created'])))
    # meetings
    for m in c.execute(sa.select(P['crm_meetings']).where(P['crm_meetings'].c.org_id == org_hex)).mappings():
        if not c.execute(sa.select(T['leads'].c.id).where(T['leads'].c.id == _uid(m['lead_id']))).first():
            continue
        starts = _iso(m['starts_at'])
        ends = _iso(m['ends_at']) or starts
        c.execute(T['meetings'].insert().values(
            id=_uid(m['id']), org_id=org_id, lead_id=_uid(m['lead_id']), organizer_id=_uid(m['owner_id']), title=m['title'],
            starts_at=starts, ends_at=ends, timezone=m['timezone'], google_event_id=m['provider_id'], meet_url=_enc(m['join_url']),
            status=m['status'] if m['status'] in ('scheduled', 'held', 'no_show', 'rescheduled', 'cancelled') else 'scheduled',
            legacy_id=m['id'], created_at=_ts(m['created'])))
    # targets + policy
    for tv in c.execute(sa.select(P['crm_target_versions'])).mappings():
        ed = date.fromisoformat(tv['effective_date'])
        if not c.execute(sa.select(T['targets'].c.id).where(T['targets'].c.org_id == org_id, T['targets'].c.effective_date == ed)).first():
            c.execute(T['targets'].insert().values(id=_uuid7(), org_id=org_id, effective_date=ed, call_target=tv['call_target'], conn_target=tv['connection_target'], bands=BANDS))
    policy = c.execute(sa.select(P['crm_settings'].c.value).where(P['crm_settings'].c.key == 'policy')).scalar_one_or_none()
    if policy and policy.get('import_edit_minutes'):
        lw = dict(DEFAULT_SETTINGS['lock_windows'])
        lw['import_minutes'] = int(policy['import_edit_minutes'])
        c.execute(T['settings'].update().where(T['settings'].c.org_id == org_id, T['settings'].c.key == 'lock_windows').values(value=lw))
    # audit -> hash-chained audit_log, anchored
    prev = '0' * 64
    anchor = {'action': 'audit.anchor', 'resource': 'migration', 'resource_id': '0003_seed_and_pilot_data', 'at': datetime.now(UTC).isoformat()}
    h = _audit_hash(prev, anchor)
    c.execute(T['audit_log'].insert().values(org_id=org_id, action='audit.anchor', resource='migration', resource_id='0003_seed_and_pilot_data', after=anchor, prev_hash=prev, hash=h))
    prev = h
    for a in c.execute(sa.select(P['crm_audit']).where(P['crm_audit'].c.org_id == org_hex).order_by(P['crm_audit'].c.created, P['crm_audit'].c.id)).mappings():
        row = {'actor_id': a['actor_id'], 'action': a['action'], 'resource': 'pilot', 'resource_id': a['resource_id'], 'after': a['detail'], 'at': _ts(a['created']).isoformat()}
        h = _audit_hash(prev, row)
        c.execute(T['audit_log'].insert().values(org_id=org_id, actor_id=_uid(a['actor_id']), action=a['action'][:80], resource='pilot', resource_id=a['resource_id'],
                                                 after={'detail': a['detail'], 'actor_name': a['actor_name'], 'owner_id': a['owner_id']},
                                                 at=_ts(a['created']), prev_hash=prev, hash=h))
        prev = h
    _rebuild_registry(c, org_id)


def _rebuild_registry(c, org_id) -> None:
    c.execute(sa.text("""
INSERT INTO phone_registry (org_id, phone_key, first_seen_activity_id, first_seen_by, first_seen_at,
  first_attempt_activity_id, first_attempt_by, first_attempt_at, first_connect_activity_id, first_connect_by, first_connect_at,
  state, last_activity_at, updated_at)
SELECT :org, k.phone_key,
  s.id, s.owner_id, s.created_at,
  a.id, a.owner_id, a.first_committed_at,
  cn.id, cn.owner_id, cn.first_committed_at,
  'none', k.last_at, now()
FROM (SELECT phone_key, max(created_at) AS last_at FROM activities WHERE org_id = :org AND phone_key IS NOT NULL AND deleted_at IS NULL GROUP BY phone_key) k
CROSS JOIN LATERAL (SELECT id, owner_id, created_at FROM activities WHERE org_id = :org AND phone_key = k.phone_key AND deleted_at IS NULL ORDER BY created_at, id LIMIT 1) s
LEFT JOIN LATERAL (SELECT id, owner_id, first_committed_at FROM activities WHERE org_id = :org AND phone_key = k.phone_key AND deleted_at IS NULL AND called IS TRUE AND first_committed_at IS NOT NULL ORDER BY first_committed_at, id LIMIT 1) a ON true
LEFT JOIN LATERAL (SELECT id, owner_id, first_committed_at FROM activities WHERE org_id = :org AND phone_key = k.phone_key AND deleted_at IS NULL AND connected IS TRUE AND first_committed_at IS NOT NULL ORDER BY first_committed_at, id LIMIT 1) cn ON true
ON CONFLICT DO NOTHING
"""), {'org': org_id})
    # pipeline state: highest stage per phone
    c.execute(sa.text("""
UPDATE phone_registry r SET state = 'followup', state_owner_id = f.owner_id
FROM followups f WHERE f.org_id = :org AND r.org_id = :org AND f.phone_key = r.phone_key AND f.status = 'open'"""), {'org': org_id})
    c.execute(sa.text("""
UPDATE phone_registry r SET state = CASE WHEN s.decision = 'confirmed' THEN 'strong_confirmed' ELSE 'strong_pending' END, state_owner_id = s.requested_by
FROM strong_followups s WHERE s.org_id = :org AND r.org_id = :org AND s.phone_key = r.phone_key AND s.decision IN ('pending', 'confirmed')"""), {'org': org_id})
    c.execute(sa.text("""
UPDATE phone_registry r SET state = CASE l.stage WHEN 'meeting' THEN 'meeting' WHEN 'signed_up' THEN 'signed' WHEN 'client' THEN 'client'
  WHEN 'agreement_in_progress' THEN 'signed' ELSE 'lead' END,
  protected_lead_id = l.id, protected_owner_id = l.owner_id, state_owner_id = l.owner_id
FROM leads l WHERE l.org_id = :org AND r.org_id = :org AND l.phone_key = r.phone_key AND l.stage <> 'lost'"""), {'org': org_id})


def downgrade() -> None:
    # Pilot tables were never modified, so removing the copied v2 rows restores the 0002 state exactly.
    for name in ['phone_registry', 'audit_log', 'meetings', 'flags', 'lead_ownership_claims', 'attribution_ledger', 'leads',
                 'strong_followups', 'followups', 'activities', 'team_memberships', 'teams', 'module_grants',
                 'user_domain_assignments', 'settings', 'targets']:
        op.execute(f'DELETE FROM {name}')
    op.execute('UPDATE projects SET owner_user_id = NULL')
    op.execute('UPDATE users SET manager_id = NULL')
    op.execute('DELETE FROM users')
    op.execute('DELETE FROM projects')
    op.execute('DELETE FROM domains')
    op.execute('DELETE FROM orgs')
    op.execute('DROP POLICY IF EXISTS org_isolation ON stored_files')
    op.drop_table('stored_files')



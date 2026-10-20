"""CRM v2 data model (master command §5). PostgreSQL 16 only.

Conventions: UUIDv7 primary keys, every business table carries org_id, created_at,
updated_at and version; timestamps are timestamptz (UTC); money is integer cents;
business_date is the Pacific date at commit. `activities` and `score_ledger` are
range-partitioned by business_date (created in migration 0002 with raw SQL, see
ADR-0004); their Table objects here describe the parent for query building.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, TIMESTAMP, UUID

metadata = MetaData(naming_convention={
    'ix': 'ix_%(table_name)s_%(column_0_N_name)s',
    'uq': 'uq_%(table_name)s_%(column_0_N_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
})

TS = TIMESTAMP(timezone=True)


def _id() -> Column:
    return Column('id', UUID(as_uuid=True), primary_key=True)


def _org() -> Column:
    return Column('org_id', UUID(as_uuid=True), ForeignKey('orgs.id'), nullable=False)


def _stamps() -> list[Column]:
    return [
        Column('created_at', TS, nullable=False, server_default=func.now()),
        Column('updated_at', TS, nullable=False, server_default=func.now()),
        Column('version', Integer, nullable=False, server_default=text('1')),
    ]


def _user_fk(name: str, nullable: bool = True) -> Column:
    return Column(name, UUID(as_uuid=True), ForeignKey('users.id'), nullable=nullable)


CRM_ROLES = ('none', 'sales_agent', 'supervisor', 'manager', 'director_admin', 'super_admin')
USER_STATUSES = ('invited', 'active', 'suspended', 'offboarded')
PIPELINE_STATES = ('none', 'followup', 'strong_pending', 'strong_confirmed', 'lead', 'meeting', 'signed', 'client')
LEAD_STAGES = ('qualified_lead', 'meeting', 'signed_up', 'agreement_in_progress', 'client', 'lost')


def _enum_check(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    quoted = ', '.join(f"'{v}'" for v in values)
    return CheckConstraint(f'{column} IN ({quoted})', name=name)


orgs = Table(
    'orgs', metadata, _id(),
    Column('name', String(200), nullable=False),
    Column('timezone', String(64), nullable=False, server_default='America/Los_Angeles'),
    *_stamps(),
)

domains = Table(
    'domains', metadata,
    Column('id', String(60), primary_key=True),
    _org(),
    Column('name', String(150), nullable=False),
    Column('legal_name', String(200)),
    Column('tfsc_entity_key', String(80)),
    Column('active', Boolean, nullable=False, server_default=text('true')),
    Column('sort', Integer, nullable=False, server_default=text('0')),
    *_stamps(),
    UniqueConstraint('org_id', 'name'),
)

projects = Table(
    'projects', metadata,
    Column('id', String(5), primary_key=True),
    _org(),
    Column('name', String(150)),
    Column('domain_id', String(60), ForeignKey('domains.id')),
    Column('owner_user_id', UUID(as_uuid=True), ForeignKey('users.id', use_alter=True)),
    Column('owner_name', String(150)),
    Column('owner_email', String(254)),
    Column('owner_phone', String(40)),
    Column('status', String(30), nullable=False, server_default='pending_configuration'),
    Column('notes', Text),
    Column('first_used_at', TS),
    *_stamps(),
    CheckConstraint(r"id ~ '^P[0-9]{4}$'", name='id_pattern'),
    _enum_check('status', ('active', 'paused', 'closed', 'pending_configuration'), 'status'),
)

users = Table(
    'users', metadata, _id(), _org(),
    Column('email', String(254), nullable=False, unique=True),
    Column('display_name', String(150), nullable=False),
    Column('first_name', String(80)),
    Column('last_name', String(80)),
    Column('phone', String(40)),
    Column('photo_object_key', String(300)),
    Column('employee_code', String(40)),
    Column('status', String(20), nullable=False, server_default='active'),
    Column('crm_role', String(20), nullable=False, server_default='sales_agent'),
    Column('manager_id', UUID(as_uuid=True), ForeignKey('users.id')),
    Column('hired_on', Date),
    Column('left_on', Date),
    Column('default_domain_id', String(60), ForeignKey('domains.id'), nullable=False, server_default='scribe_align'),
    Column('default_project_id', String(5), ForeignKey('projects.id')),
    Column('vonage_extension', String(40)),
    Column('mfa_required', Boolean, nullable=False, server_default=text('false')),
    Column('routing_view', Boolean, nullable=False, server_default=text('false')),
    Column('last_login_at', TS),
    Column('password_hash', Text),  # break-glass/local login only (ADR-0014)
    Column('reset_required', Boolean, nullable=False, server_default=text('false')),
    Column('legacy_id', String(32), unique=True),  # pilot crm_users.id
    *_stamps(),
    _enum_check('status', USER_STATUSES, 'status'),
    _enum_check('crm_role', CRM_ROLES, 'crm_role'),
    Index('ix_users_org_manager', 'org_id', 'manager_id'),
)

user_sessions = Table(
    'user_sessions', metadata,
    Column('token_hash', String(64), primary_key=True),
    _user_fk('user_id', nullable=False),
    Column('csrf', String(64), nullable=False),
    Column('created_at', TS, nullable=False, server_default=func.now()),
    Column('last_seen_at', TS, nullable=False, server_default=func.now()),
    Column('expires_at', TS, nullable=False),  # absolute (12 h)
    Column('idle_expires_at', TS, nullable=False),  # idle (30 min)
    Column('ip', INET),
    Column('user_agent', String(300)),
    Column('revoked_at', TS),
    Index('ix_user_sessions_user', 'user_id'),
)

oidc_identities = Table(
    'oidc_identities', metadata,
    Column('issuer', String(500), nullable=False),
    Column('subject', String(250), nullable=False),
    _user_fk('user_id', nullable=False),
    Column('created_at', TS, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint('issuer', 'subject'),
)

user_domain_assignments = Table(
    'user_domain_assignments', metadata, _id(), _org(),
    _user_fk('user_id', nullable=False),
    Column('domain_id', String(60), ForeignKey('domains.id'), nullable=False),
    Column('project_id', String(5), ForeignKey('projects.id')),
    Column('effective_from', Date, nullable=False),
    Column('effective_to', Date),
    _user_fk('set_by'),
    Column('created_at', TS, nullable=False, server_default=func.now()),
    Index('ix_user_domain_assignments_user', 'user_id', 'effective_from'),
)

teams = Table(
    'teams', metadata, _id(), _org(),
    Column('name', String(150), nullable=False),
    Column('kind', String(10), nullable=False, server_default='team'),
    _user_fk('supervisor_id', nullable=False),
    _user_fk('leader_id'),
    Column('parent_team_id', UUID(as_uuid=True), ForeignKey('teams.id')),
    Column('active', Boolean, nullable=False, server_default=text('true')),
    Column('legacy_id', String(32), unique=True),
    *_stamps(),
    _enum_check('kind', ('team', 'group'), 'kind'),
)

team_memberships = Table(
    'team_memberships', metadata, _id(), _org(),
    Column('team_id', UUID(as_uuid=True), ForeignKey('teams.id'), nullable=False),
    _user_fk('user_id', nullable=False),
    Column('effective_from', Date, nullable=False),
    Column('effective_to', Date),
    Column('created_at', TS, nullable=False, server_default=func.now()),
    Index('ix_team_memberships_team', 'team_id', 'effective_from'),
    Index('ix_team_memberships_user', 'user_id', 'effective_from'),
)

module_grants = Table(
    'module_grants', metadata, _id(), _org(),
    _user_fk('user_id', nullable=False),
    Column('module', String(20), nullable=False),
    Column('role', String(30), nullable=False),
    Column('companies', JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column('parent_admin_ref', String(64)),
    Column('external_account_id', String(64)),  # TFSC account id returned by provisioning
    Column('status', String(20), nullable=False, server_default='active'),
    _user_fk('granted_by'),
    Column('granted_at', TS, nullable=False, server_default=func.now()),
    Column('revoked_at', TS),
    _enum_check('module', ('sales', 'agreements'), 'module'),
    Index('ix_module_grants_user', 'user_id', 'module'),
)

prospects = Table(
    'prospects', metadata, _id(), _org(),
    Column('kind', String(10), nullable=False, server_default='unknown'),
    Column('display_name', String(200), nullable=False),
    Column('legal_name', String(200)),
    Column('credentials', ARRAY(String(20)), nullable=False, server_default=text("'{}'")),
    Column('entity_suffix', String(20)),
    Column('npi', String(10)),
    Column('address', JSONB),
    Column('domain_id', String(60), ForeignKey('domains.id')),
    Column('project_id', String(5), ForeignKey('projects.id')),
    Column('first_seen_activity_id', UUID(as_uuid=True)),
    Column('name_key', String(200)),  # normalized for trigram similarity (secondary signal)
    *_stamps(),
)

contact_points = Table(
    'contact_points', metadata, _id(), _org(),
    Column('prospect_id', UUID(as_uuid=True), ForeignKey('prospects.id'), nullable=False),
    Column('kind', String(10), nullable=False),
    Column('value_display', Text, nullable=False),  # encrypted in hosted mode
    Column('value_key', String(64), nullable=False),  # blind index
    Column('extension', String(10)),
    Column('label', String(40)),
    Column('is_shared_line', Boolean, nullable=False, server_default=text('false')),
    *_stamps(),
    Index('ix_contact_points_key', 'org_id', 'kind', 'value_key'),
)

phone_registry = Table(
    'phone_registry', metadata, _org(),
    Column('phone_key', String(64), nullable=False),
    Column('first_seen_activity_id', UUID(as_uuid=True)),
    _user_fk('first_seen_by'),
    Column('first_seen_at', TS),
    Column('first_attempt_activity_id', UUID(as_uuid=True)),
    _user_fk('first_attempt_by'),
    Column('first_attempt_at', TS),
    Column('first_connect_activity_id', UUID(as_uuid=True)),
    _user_fk('first_connect_by'),
    Column('first_connect_at', TS),
    Column('state', String(20), nullable=False, server_default='none'),
    Column('state_owner_id', UUID(as_uuid=True), ForeignKey('users.id')),  # owner of the current follow-up/strong state
    Column('protected_lead_id', UUID(as_uuid=True)),
    _user_fk('protected_owner_id'),
    Column('last_activity_at', TS),
    Column('updated_at', TS, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint('org_id', 'phone_key'),
    _enum_check('state', PIPELINE_STATES, 'state'),
)

# Partitioned parent (DDL in migration 0002). PK must include the partition key.
activities = Table(
    'activities', metadata,
    Column('id', UUID(as_uuid=True), nullable=False),
    _org(),
    _user_fk('owner_id', nullable=False),
    Column('prospect_id', UUID(as_uuid=True)),
    Column('business_date', Date, nullable=False),
    Column('entry_source', String(20), nullable=False),
    Column('import_batch_id', UUID(as_uuid=True)),
    Column('raw', JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column('provider_name', String(200), nullable=False),
    Column('prospect_kind', String(10)),
    Column('phone_display', Text),
    Column('phone_key', String(64)),
    Column('extension', String(10)),
    Column('email', Text),
    Column('email_key', String(64)),
    Column('called', Boolean),
    Column('connected', Boolean),
    Column('text_sent', Boolean),
    Column('email_sent', Boolean),
    Column('followup_needed', Boolean),
    Column('next_followup_at', TS),
    Column('remarks', Text, nullable=False, server_default=''),
    Column('first_committed_at', TS),
    Column('edit_deadline', TS),
    Column('lock_window_minutes', SmallInteger),
    Column('conflict', String(10)),  # NULL | amber | red
    Column('deleted_at', TS),
    _user_fk('deleted_by'),
    Column('delete_reason', Text),
    Column('domain_id', String(60)),
    Column('project_id', String(5)),
    Column('score_points', SmallInteger, nullable=False, server_default=text('0')),
    Column('legacy_id', String(32)),
    *_stamps(),
    PrimaryKeyConstraint('id', 'business_date'),
    Index('ix_activities_owner_date', 'org_id', 'owner_id', 'business_date'),
    Index('ix_activities_phone', 'org_id', 'phone_key'),
    Index('ix_activities_email', 'org_id', 'email_key'),
    Index('ix_activities_prospect', 'prospect_id'),
    Index('ix_activities_created_brin', 'created_at', postgresql_using='brin'),
    Index('ix_activities_legacy', 'legacy_id'),
    postgresql_partition_by='RANGE (business_date)',
)

activity_revisions = Table(
    'activity_revisions', metadata, _id(), _org(),
    Column('activity_id', UUID(as_uuid=True), nullable=False),
    Column('business_date', Date, nullable=False),
    Column('rev', Integer, nullable=False),
    _user_fk('changed_by', nullable=False),
    Column('reason', Text),
    Column('before', JSONB, nullable=False),
    Column('after', JSONB, nullable=False),
    Column('at', TS, nullable=False, server_default=func.now()),
    UniqueConstraint('activity_id', 'rev'),
)

followups = Table(
    'followups', metadata, _id(), _org(),
    Column('activity_id', UUID(as_uuid=True)),
    Column('activity_date', Date),
    Column('prospect_id', UUID(as_uuid=True), ForeignKey('prospects.id')),
    Column('phone_key', String(64)),
    _user_fk('owner_id', nullable=False),
    Column('due_at', TS),  # NULL = backlog ("Call Again?")
    Column('status', String(12), nullable=False, server_default='open'),
    Column('outcome_note', Text),
    Column('delegated_group_id', UUID(as_uuid=True), ForeignKey('teams.id')),
    _user_fk('completed_by'),
    Column('completed_at', TS),
    *_stamps(),
    Column('escalated_at', TS),  # overdue 3+ days: supervisor notified once (§13)
    _enum_check('status', ('open', 'done', 'missed', 'cancelled'), 'status'),
    Index('ix_followups_owner_due', 'org_id', 'owner_id', 'status', 'due_at'),
)

followup_notes = Table(
    'followup_notes', metadata, _id(), _org(),
    Column('followup_id', UUID(as_uuid=True), ForeignKey('followups.id'), nullable=False),
    _user_fk('author_id', nullable=False),
    Column('body', Text, nullable=False),
    Column('created_at', TS, nullable=False, server_default=func.now()),
)

strong_followups = Table(
    'strong_followups', metadata, _id(), _org(),
    Column('prospect_id', UUID(as_uuid=True), ForeignKey('prospects.id')),
    Column('phone_key', String(64)),
    Column('activity_id', UUID(as_uuid=True), nullable=False),
    Column('activity_date', Date, nullable=False),
    _user_fk('requested_by', nullable=False),
    Column('requested_at', TS, nullable=False, server_default=func.now()),
    _user_fk('supervisor_id'),
    Column('decision', String(12), nullable=False, server_default='pending'),
    _user_fk('decided_by'),
    Column('decided_at', TS),
    Column('reason', Text),
    Column('target_window', String(80)),
    Column('note', Text),
    *_stamps(),
    _enum_check('decision', ('pending', 'confirmed', 'rejected'), 'decision'),
)

strong_followup_comments = Table(
    'strong_followup_comments', metadata, _id(), _org(),
    Column('strong_followup_id', UUID(as_uuid=True), ForeignKey('strong_followups.id'), nullable=False),
    _user_fk('author_id', nullable=False),
    Column('body', Text, nullable=False),
    Column('created_at', TS, nullable=False, server_default=func.now()),
)

leads = Table(
    'leads', metadata, _id(), _org(),
    Column('prospect_id', UUID(as_uuid=True), ForeignKey('prospects.id')),
    Column('origin_activity_id', UUID(as_uuid=True), nullable=False),
    Column('origin_activity_date', Date, nullable=False),
    Column('phone_key', String(64)),
    _user_fk('owner_id', nullable=False),
    _user_fk('supervisor_id'),
    Column('stage', String(30), nullable=False, server_default='qualified_lead'),
    Column('stage_changed_at', TS, nullable=False, server_default=func.now()),
    _user_fk('qualified_by'),
    Column('qualified_at', TS),
    _user_fk('signed_by'),
    Column('signed_at', TS),
    Column('lost_reason', Text),
    Column('domain_id', String(60)),
    Column('project_id', String(5)),
    Column('agreement_locked_at', TS),
    Column('agreement_status', String(30)),
    Column('legacy_id', String(32), unique=True),
    *_stamps(),
    _enum_check('stage', LEAD_STAGES, 'stage'),
    Index('ix_leads_owner_stage', 'org_id', 'owner_id', 'stage'),
)

lead_ownership_claims = Table(
    'lead_ownership_claims', metadata, _org(),
    Column('phone_key', String(64), nullable=False),
    Column('lead_id', UUID(as_uuid=True), ForeignKey('leads.id'), nullable=False, unique=True),
    _user_fk('owner_id', nullable=False),
    Column('claimed_at', TS, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint('org_id', 'phone_key'),
)

lead_assignments = Table(
    'lead_assignments', metadata, _id(), _org(),
    Column('lead_id', UUID(as_uuid=True), ForeignKey('leads.id'), nullable=False),
    _user_fk('from_user'),
    _user_fk('to_user', nullable=False),
    Column('reason', Text, nullable=False),
    Column('incentive_split', JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    _user_fk('by_user', nullable=False),
    Column('at', TS, nullable=False, server_default=func.now()),
)

attribution_ledger = Table(
    'attribution_ledger', metadata, _id(), _org(),
    Column('lead_id', UUID(as_uuid=True), ForeignKey('leads.id'), nullable=False),
    _user_fk('user_id', nullable=False),
    Column('role', String(20), nullable=False),
    Column('share', Numeric(5, 4), nullable=False, server_default=text('0')),
    Column('source_activity_id', UUID(as_uuid=True)),
    Column('at', TS, nullable=False, server_default=func.now()),
    _enum_check('role', ('first_seen', 'first_attempt', 'first_connect', 'nurture', 'qualifier', 'closer', 'supervisor'), 'role'),
    Index('ix_attribution_ledger_lead', 'lead_id'),
)

flags = Table(
    'flags', metadata, _id(), _org(),
    Column('severity', String(8), nullable=False),
    Column('kind', String(40), nullable=False),
    Column('phone_key', String(64)),
    Column('activity_id', UUID(as_uuid=True)),
    Column('other_activity_id', UUID(as_uuid=True)),
    Column('lead_id', UUID(as_uuid=True)),
    _user_fk('raised_for_user'),
    _user_fk('other_user'),
    _user_fk('assigned_to_supervisor'),
    Column('detail', JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column('status', String(10), nullable=False, server_default='open'),
    Column('resolution', String(30)),
    Column('resolution_note', Text),
    _user_fk('resolved_by'),
    Column('resolved_at', TS),
    Column('escalated_at', TS),
    *_stamps(),
    _enum_check('severity', ('info', 'amber', 'red'), 'severity'),
    _enum_check('status', ('open', 'ack', 'resolved'), 'status'),
    Index('ix_flags_open', 'org_id', 'status', 'severity', 'created_at'),
)

meetings = Table(
    'meetings', metadata, _id(), _org(),
    Column('lead_id', UUID(as_uuid=True), ForeignKey('leads.id'), nullable=False),
    _user_fk('organizer_id', nullable=False),
    Column('title', String(200), nullable=False),
    Column('starts_at', TS, nullable=False),
    Column('ends_at', TS, nullable=False),
    Column('timezone', String(64), nullable=False, server_default='America/Los_Angeles'),
    Column('google_event_id', String(200)),
    Column('meet_url', Text),  # encrypted at rest
    Column('attendees', Text),  # encrypted JSON
    Column('status', String(15), nullable=False, server_default='scheduled'),
    Column('notes', Text),
    Column('legacy_id', String(32), unique=True),
    *_stamps(),
    _enum_check('status', ('scheduled', 'held', 'no_show', 'rescheduled', 'cancelled'), 'status'),
)

# Partitioned parent (DDL in migration 0002).
score_ledger = Table(
    'score_ledger', metadata,
    Column('id', UUID(as_uuid=True), nullable=False),
    _org(),
    Column('activity_id', UUID(as_uuid=True), nullable=False),
    Column('activity_date', Date, nullable=False),
    _user_fk('user_id', nullable=False),
    Column('business_date', Date, nullable=False),
    Column('delta_points', SmallInteger, nullable=False),
    Column('reason', String(12), nullable=False),
    Column('rule_version', String(20), nullable=False, server_default='score.v1'),
    Column('at', TS, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint('id', 'business_date'),
    Index('ix_score_ledger_user_date', 'org_id', 'user_id', 'business_date'),
    Index('ix_score_ledger_activity', 'activity_id'),
    postgresql_partition_by='RANGE (business_date)',
)

targets = Table(
    'targets', metadata, _id(), _org(),
    Column('effective_date', Date, nullable=False),
    Column('call_target', Integer, nullable=False),
    Column('conn_target', Numeric(5, 4), nullable=False),
    Column('quality_target', Numeric(5, 4)),
    Column('bands', JSONB, nullable=False),
    _user_fk('set_by'),
    Column('created_at', TS, nullable=False, server_default=func.now()),
    UniqueConstraint('org_id', 'effective_date'),
)

work_calendar = Table(
    'work_calendar', metadata, _org(),
    Column('date', Date, nullable=False),
    Column('is_workday', Boolean, nullable=False),
    Column('note', String(200)),
    PrimaryKeyConstraint('org_id', 'date'),
)

user_leave = Table(
    'user_leave', metadata,
    _user_fk('user_id', nullable=False),
    Column('date', Date, nullable=False),
    Column('kind', String(20), nullable=False, server_default='leave'),
    PrimaryKeyConstraint('user_id', 'date'),
)

agent_day_stats = Table(
    'agent_day_stats', metadata, _org(),
    _user_fk('user_id', nullable=False),
    Column('business_date', Date, nullable=False),
    Column('team_id', UUID(as_uuid=True)),
    Column('domain_id', String(60)),
    Column('project_id', String(5)),
    Column('attempts', Integer, nullable=False, server_default=text('0')),
    Column('connected', Integer, nullable=False, server_default=text('0')),
    Column('sms', Integer, nullable=False, server_default=text('0')),
    Column('emails', Integer, nullable=False, server_default=text('0')),
    Column('any_touch', Integer, nullable=False, server_default=text('0')),
    Column('research', Integer, nullable=False, server_default=text('0')),
    Column('no_call', Integer, nullable=False, server_default=text('0')),
    Column('points', Integer, nullable=False, server_default=text('0')),
    Column('points_event', Integer, nullable=False, server_default=text('0')),
    Column('followups_created', Integer, nullable=False, server_default=text('0')),
    Column('strong_requested', Integer, nullable=False, server_default=text('0')),
    Column('strong_confirmed', Integer, nullable=False, server_default=text('0')),
    Column('leads', Integer, nullable=False, server_default=text('0')),
    Column('signups', Integer, nullable=False, server_default=text('0')),
    Column('duplicates', Integer, nullable=False, server_default=text('0')),
    Column('red_flags', Integer, nullable=False, server_default=text('0')),
    Column('post_lock_edits', Integer, nullable=False, server_default=text('0')),
    Column('last_activity_at', TS),
    Column('updated_at', TS, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint('org_id', 'user_id', 'business_date'),
    Index('ix_agent_day_stats_date', 'org_id', 'business_date'),
)

agent_hour_stats = Table(
    'agent_hour_stats', metadata, _org(),
    _user_fk('user_id', nullable=False),
    Column('business_date', Date, nullable=False),
    Column('hour', SmallInteger, nullable=False),  # Pacific hour 0-23
    Column('attempts', Integer, nullable=False, server_default=text('0')),
    Column('connected', Integer, nullable=False, server_default=text('0')),
    Column('points', Integer, nullable=False, server_default=text('0')),
    PrimaryKeyConstraint('org_id', 'user_id', 'business_date', 'hour'),
)

clients = Table(
    'clients', metadata, _id(), _org(),
    Column('prospect_id', UUID(as_uuid=True), ForeignKey('prospects.id')),
    Column('lead_id', UUID(as_uuid=True), ForeignKey('leads.id'), unique=True),
    Column('display_name', String(200), nullable=False),
    Column('legal_name', String(200)),
    Column('domain_id', String(60)),
    Column('project_id', String(5)),
    Column('tfsc_client_id', String(64)),
    Column('agreement_status', String(30)),
    Column('primary_contact', JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column('address', JSONB),
    _user_fk('account_owner_id'),
    _user_fk('supervisor_id'),
    Column('sale_date', Date),
    *_stamps(),
)

import_batches = Table(
    'import_batches', metadata, _id(), _org(),
    _user_fk('owner_id', nullable=False),
    _user_fk('created_by', nullable=False),
    Column('kind', String(20), nullable=False, server_default='paste'),  # paste | file | legacy
    Column('request_key', String(80)),
    Column('body_hash', String(64)),
    Column('file_object_key', String(300)),
    Column('sha256', String(64)),
    Column('sheet', String(120)),
    Column('status', String(20), nullable=False, server_default='pending'),
    Column('totals', JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column('mapping', JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column('result', JSONB),
    *_stamps(),
    UniqueConstraint('owner_id', 'request_key'),
)

import_rows = Table(
    'import_rows', metadata, _org(),
    Column('batch_id', UUID(as_uuid=True), ForeignKey('import_batches.id'), nullable=False),
    Column('row_no', Integer, nullable=False),
    Column('raw', JSONB, nullable=False),
    Column('refined', JSONB),
    Column('issues', JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column('decision', String(20), nullable=False, server_default='pending'),
    Column('activity_id', UUID(as_uuid=True)),
    PrimaryKeyConstraint('batch_id', 'row_no'),
)

notifications = Table(
    'notifications', metadata, _id(), _org(),
    _user_fk('user_id', nullable=False),
    Column('kind', String(40), nullable=False),
    Column('payload', JSONB, nullable=False),
    Column('read_at', TS),
    Column('created_at', TS, nullable=False, server_default=func.now()),
    Index('ix_notifications_user', 'user_id', 'read_at', 'created_at'),
)

dashboard_layouts = Table(
    'dashboard_layouts', metadata, _id(), _org(),
    _user_fk('user_id'),
    Column('template_name', String(80)),
    Column('layout', JSONB, nullable=False),
    *_stamps(),
    UniqueConstraint('user_id'),
)

celebrations = Table(
    'celebrations', metadata, _id(), _org(),
    Column('lead_id', UUID(as_uuid=True), ForeignKey('leads.id'), nullable=False),
    _user_fk('agent_id', nullable=False),
    _user_fk('supervisor_id'),
    Column('message', Text, nullable=False),
    Column('created_at', TS, nullable=False, server_default=func.now()),
)

settings = Table(
    'settings', metadata, _org(),
    Column('key', String(80), nullable=False),
    Column('value', JSONB, nullable=False),
    Column('updated_at', TS, nullable=False, server_default=func.now()),
    _user_fk('updated_by'),
    PrimaryKeyConstraint('org_id', 'key'),
)

outbox = Table(
    'outbox', metadata,
    Column('id', BigInteger, primary_key=True, autoincrement=True),
    Column('event_id', UUID(as_uuid=True), nullable=False, unique=True),
    _org(),
    Column('topic', String(80), nullable=False),
    Column('key', String(120)),
    Column('payload', JSONB, nullable=False),
    Column('schema_version', SmallInteger, nullable=False, server_default=text('1')),
    Column('state', String(12), nullable=False, server_default='pending'),
    Column('attempts', Integer, nullable=False, server_default=text('0')),
    Column('next_attempt_at', TS, nullable=False, server_default=func.now()),
    Column('last_error', Text),
    Column('created_at', TS, nullable=False, server_default=func.now()),
    Column('delivered_at', TS),
    _enum_check('state', ('pending', 'delivered', 'dead'), 'state'),
    Index('ix_outbox_due', 'state', 'next_attempt_at'),
)

inbox = Table(
    'inbox', metadata,
    Column('id', BigInteger, primary_key=True, autoincrement=True),
    Column('source', String(40), nullable=False),
    Column('event_id', String(80), nullable=False),
    Column('topic', String(80), nullable=False),
    Column('payload', JSONB, nullable=False),
    Column('received_at', TS, nullable=False, server_default=func.now()),
    Column('processed_at', TS),
    Column('error', Text),
    UniqueConstraint('source', 'event_id'),
)

audit_log = Table(
    'audit_log', metadata,
    Column('id', BigInteger, primary_key=True, autoincrement=True),
    _org(),
    Column('actor_id', UUID(as_uuid=True)),
    Column('action', String(80), nullable=False),
    Column('resource', String(40), nullable=False),
    Column('resource_id', String(64)),
    Column('ip', INET),
    Column('ua', String(300)),
    Column('before', JSONB),
    Column('after', JSONB),
    Column('at', TS, nullable=False, server_default=func.now()),
    Column('prev_hash', String(64), nullable=False),
    Column('hash', String(64), nullable=False, unique=True),
    Index('ix_audit_log_resource', 'org_id', 'resource', 'resource_id'),
)

data_access_log = Table(
    'data_access_log', metadata,
    Column('id', BigInteger, primary_key=True, autoincrement=True),
    _org(),
    Column('actor_id', UUID(as_uuid=True), nullable=False),
    Column('resource', String(40), nullable=False),
    Column('count', Integer, nullable=False),
    Column('distinct_keys', ARRAY(String(64))),
    Column('at', TS, nullable=False, server_default=func.now()),
    Index('ix_data_access_log_actor', 'actor_id', 'at'),
)

google_connections = Table(
    'google_connections', metadata,
    _user_fk('user_id', nullable=False),
    _org(),
    Column('encrypted_refresh_token', LargeBinary, nullable=False),
    Column('calendar_id', String(200), nullable=False, server_default='primary'),
    Column('sync_token', Text),
    Column('channel_id', String(100)),
    Column('channel_expires_at', TS),
    Column('created_at', TS, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint('user_id'),
)

exports = Table(
    'exports', metadata, _id(), _org(),
    _user_fk('requested_by', nullable=False),
    _user_fk('approved_by'),
    Column('kind', String(40), nullable=False),
    Column('filters', JSONB, nullable=False),
    Column('row_count', Integer),
    Column('status', String(20), nullable=False, server_default='requested'),
    Column('object_key', String(300)),
    Column('watermark', String(200)),
    Column('reason', Text),
    Column('approved_at', TS),
    Column('expires_at', TS),
    Column('file_sha256', String(64)),
    Column('password_enc', Text),  # one-time ZIP password, encrypted at rest; revealed once to the requester
    Column('password_revealed_at', TS),
    Column('downloads', Integer, nullable=False, server_default=text('0')),
    Column('error', Text),
    *_stamps(),
)

audit_anchors = Table(
    'audit_anchors', metadata,
    Column('id', BigInteger, primary_key=True, autoincrement=True),
    _org(),
    Column('anchored_for', Date, nullable=False),
    Column('last_audit_id', BigInteger, nullable=False),
    Column('head_hash', String(64), nullable=False),
    Column('rows', Integer, nullable=False),
    Column('sink', String(40), nullable=False),
    Column('sink_ref', String(300)),
    Column('created_at', TS, nullable=False, server_default=func.now()),
    UniqueConstraint('org_id', 'anchored_for'),
)

# Scheduler bookkeeping (not org data): one row per job and period makes every job idempotent across nodes.
job_runs = Table(
    'job_runs', metadata,
    Column('job', String(60), nullable=False),
    Column('period_key', String(40), nullable=False),
    Column('started_at', TS, nullable=False, server_default=func.now()),
    Column('finished_at', TS),
    Column('status', String(12), nullable=False, server_default='running'),
    Column('detail', JSONB),
    PrimaryKeyConstraint('job', 'period_key'),
)

# Created in migration 0003. DB-backed object store for local/single-node mode (photos, import files);
# hosted deployments may switch to S3-compatible storage (ADR-0015).
stored_files = Table(
    'stored_files', metadata,
    Column('key', String(300), primary_key=True),
    _org(),
    Column('content_type', String(100), nullable=False),
    Column('size', Integer, nullable=False),
    Column('sha256', String(64), nullable=False),
    Column('data', LargeBinary, nullable=False),
    Column('created_at', TS, nullable=False, server_default=func.now()),
)

# Tables guarded by Row-Level Security (ADR-0007): every table with org_id.
RLS_TABLES = [t.name for t in metadata.sorted_tables if 'org_id' in t.c and t.name != 'orgs']

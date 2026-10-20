"""CRM v2 foundation schema (master command §5): v2 tables beside the pilot tables.

- New tables only; pilot `crm_*` tables are untouched (data copied by 0003).
- `activities` and `score_ledger` are RANGE-partitioned by business_date (ADR-0004);
  `crm_ensure_partitions(months_back, months_ahead)` creates monthly partitions + a DEFAULT.
- Row-Level Security on every org-scoped table keyed on `app.org_id` (ADR-0007).
- Extensions: pg_trgm (fuzzy practice-name signal).

Revision ID: 0002_v2_foundation
Revises: 0001_pilot_baseline
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision = '0002_v2_foundation'
down_revision = '0001_pilot_baseline'
branch_labels = None
depends_on = None

PARTITIONED = ('activities', 'score_ledger')

PARTITION_FN = """
CREATE OR REPLACE FUNCTION crm_ensure_partitions(months_back int DEFAULT 2, months_ahead int DEFAULT 3)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  parent text; m date; start_m date; stop_m date; part text;
BEGIN
  start_m := date_trunc('month', (now() AT TIME ZONE 'America/Los_Angeles')::date - make_interval(months => months_back))::date;
  stop_m := date_trunc('month', (now() AT TIME ZONE 'America/Los_Angeles')::date + make_interval(months => months_ahead))::date;
  FOREACH parent IN ARRAY ARRAY['activities', 'score_ledger'] LOOP
    m := start_m;
    WHILE m <= stop_m LOOP
      part := format('%s_%s', parent, to_char(m, 'YYYY_MM'));
      IF to_regclass(part) IS NULL THEN
        EXECUTE format('CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)', part, parent, m, (m + interval '1 month')::date);
      END IF;
      m := (m + interval '1 month')::date;
    END LOOP;
    IF to_regclass(parent || '_default') IS NULL THEN
      EXECUTE format('CREATE TABLE %I PARTITION OF %I DEFAULT', parent || '_default', parent);
    END IF;
  END LOOP;
END $$;
"""

# Backfills of historical data (e.g. 2026 workbook) land in monthly partitions created on demand.
PARTITION_FOR_FN = """
CREATE OR REPLACE FUNCTION crm_ensure_partition_for(parent text, d date)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE m date := date_trunc('month', d)::date; part text := format('%s_%s', parent, to_char(date_trunc('month', d), 'YYYY_MM'));
BEGIN
  IF to_regclass(part) IS NULL THEN
    EXECUTE format('CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)', part, parent, m, (m + interval '1 month')::date);
  END IF;
END $$;
"""


def _rls_tables() -> list[str]:
    from sqlalchemy import inspect
    insp = inspect(op.get_bind())
    out = []
    for name in insp.get_table_names():
        if name.startswith(('crm_', 'oidc_')) or name in ('alembic_version', 'orgs'):
            continue
        if any(c['name'] == 'org_id' for c in insp.get_columns(name)):
            out.append(name)
    return sorted(set(out) | set(PARTITIONED))


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    op.create_table('inbox',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('source', sa.String(length=40), nullable=False),
    sa.Column('event_id', sa.String(length=80), nullable=False),
    sa.Column('topic', sa.String(length=80), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('received_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('processed_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_inbox')),
    sa.UniqueConstraint('source', 'event_id', name=op.f('uq_inbox_source_event_id'))
    )
    op.create_table('orgs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('timezone', sa.String(length=64), server_default='America/Los_Angeles', nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orgs'))
    )
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('actor_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('resource', sa.String(length=40), nullable=False),
    sa.Column('resource_id', sa.String(length=64), nullable=True),
    sa.Column('ip', postgresql.INET(), nullable=True),
    sa.Column('ua', sa.String(length=300), nullable=True),
    sa.Column('before', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('after', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('prev_hash', sa.String(length=64), nullable=False),
    sa.Column('hash', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_audit_log_org_id_orgs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log')),
    sa.UniqueConstraint('hash', name=op.f('uq_audit_log_hash'))
    )
    op.create_index('ix_audit_log_resource', 'audit_log', ['org_id', 'resource', 'resource_id'], unique=False)
    op.create_table('data_access_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('actor_id', sa.UUID(), nullable=False),
    sa.Column('resource', sa.String(length=40), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.Column('distinct_keys', postgresql.ARRAY(sa.String(length=64)), nullable=True),
    sa.Column('at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_data_access_log_org_id_orgs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_data_access_log'))
    )
    op.create_index('ix_data_access_log_actor', 'data_access_log', ['actor_id', 'at'], unique=False)
    op.create_table('domains',
    sa.Column('id', sa.String(length=60), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('legal_name', sa.String(length=200), nullable=True),
    sa.Column('tfsc_entity_key', sa.String(length=80), nullable=True),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('sort', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_domains_org_id_orgs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_domains')),
    sa.UniqueConstraint('org_id', 'name', name=op.f('uq_domains_org_id_name'))
    )
    op.create_table('outbox',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('event_id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('topic', sa.String(length=80), nullable=False),
    sa.Column('key', sa.String(length=120), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('schema_version', sa.SmallInteger(), server_default=sa.text('1'), nullable=False),
    sa.Column('state', sa.String(length=12), server_default='pending', nullable=False),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('next_attempt_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('delivered_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.CheckConstraint("state IN ('pending', 'delivered', 'dead')", name=op.f('ck_outbox_state')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_outbox_org_id_orgs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outbox')),
    sa.UniqueConstraint('event_id', name=op.f('uq_outbox_event_id'))
    )
    op.create_index('ix_outbox_due', 'outbox', ['state', 'next_attempt_at'], unique=False)
    op.create_table('work_calendar',
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('is_workday', sa.Boolean(), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_work_calendar_org_id_orgs')),
    sa.PrimaryKeyConstraint('org_id', 'date', name=op.f('pk_work_calendar'))
    )
    op.create_table('projects',
    sa.Column('id', sa.String(length=5), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=True),
    sa.Column('domain_id', sa.String(length=60), nullable=True),
    sa.Column('owner_user_id', sa.UUID(), nullable=True),
    sa.Column('owner_name', sa.String(length=150), nullable=True),
    sa.Column('owner_email', sa.String(length=254), nullable=True),
    sa.Column('owner_phone', sa.String(length=40), nullable=True),
    sa.Column('status', sa.String(length=30), server_default='pending_configuration', nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('first_used_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("id ~ '^P[0-9]{4}$'", name=op.f('ck_projects_id_pattern')),
    sa.CheckConstraint("status IN ('active', 'paused', 'closed', 'pending_configuration')", name=op.f('ck_projects_status')),
    sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name=op.f('fk_projects_domain_id_domains')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_projects_org_id_orgs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_projects'))
    )
    op.create_table('prospects',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=10), server_default='unknown', nullable=False),
    sa.Column('display_name', sa.String(length=200), nullable=False),
    sa.Column('legal_name', sa.String(length=200), nullable=True),
    sa.Column('credentials', postgresql.ARRAY(sa.String(length=20)), server_default=sa.text("'{}'"), nullable=False),
    sa.Column('entity_suffix', sa.String(length=20), nullable=True),
    sa.Column('npi', sa.String(length=10), nullable=True),
    sa.Column('address', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('domain_id', sa.String(length=60), nullable=True),
    sa.Column('project_id', sa.String(length=5), nullable=True),
    sa.Column('first_seen_activity_id', sa.UUID(), nullable=True),
    sa.Column('name_key', sa.String(length=200), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name=op.f('fk_prospects_domain_id_domains')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_prospects_org_id_orgs')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_prospects_project_id_projects')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_prospects'))
    )
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('display_name', sa.String(length=150), nullable=False),
    sa.Column('first_name', sa.String(length=80), nullable=True),
    sa.Column('last_name', sa.String(length=80), nullable=True),
    sa.Column('phone', sa.String(length=40), nullable=True),
    sa.Column('photo_object_key', sa.String(length=300), nullable=True),
    sa.Column('employee_code', sa.String(length=40), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='active', nullable=False),
    sa.Column('crm_role', sa.String(length=20), server_default='sales_agent', nullable=False),
    sa.Column('manager_id', sa.UUID(), nullable=True),
    sa.Column('hired_on', sa.Date(), nullable=True),
    sa.Column('left_on', sa.Date(), nullable=True),
    sa.Column('default_domain_id', sa.String(length=60), server_default='scribe_align', nullable=False),
    sa.Column('default_project_id', sa.String(length=5), nullable=True),
    sa.Column('vonage_extension', sa.String(length=40), nullable=True),
    sa.Column('mfa_required', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('routing_view', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('last_login_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('password_hash', sa.Text(), nullable=True),
    sa.Column('reset_required', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('legacy_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("crm_role IN ('none', 'sales_agent', 'supervisor', 'manager', 'director_admin', 'super_admin')", name=op.f('ck_users_crm_role')),
    sa.CheckConstraint("status IN ('invited', 'active', 'suspended', 'offboarded')", name=op.f('ck_users_status')),
    sa.ForeignKeyConstraint(['default_domain_id'], ['domains.id'], name=op.f('fk_users_default_domain_id_domains')),
    sa.ForeignKeyConstraint(['default_project_id'], ['projects.id'], name=op.f('fk_users_default_project_id_projects')),
    sa.ForeignKeyConstraint(['manager_id'], ['users.id'], name=op.f('fk_users_manager_id_users')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_users_org_id_orgs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email')),
    sa.UniqueConstraint('legacy_id', name=op.f('uq_users_legacy_id'))
    )
    op.create_index('ix_users_org_manager', 'users', ['org_id', 'manager_id'], unique=False)
    op.create_table('activities',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('prospect_id', sa.UUID(), nullable=True),
    sa.Column('business_date', sa.Date(), nullable=False),
    sa.Column('entry_source', sa.String(length=20), nullable=False),
    sa.Column('import_batch_id', sa.UUID(), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('provider_name', sa.String(length=200), nullable=False),
    sa.Column('prospect_kind', sa.String(length=10), nullable=True),
    sa.Column('phone_display', sa.Text(), nullable=True),
    sa.Column('phone_key', sa.String(length=64), nullable=True),
    sa.Column('extension', sa.String(length=10), nullable=True),
    sa.Column('email', sa.Text(), nullable=True),
    sa.Column('email_key', sa.String(length=64), nullable=True),
    sa.Column('called', sa.Boolean(), nullable=True),
    sa.Column('connected', sa.Boolean(), nullable=True),
    sa.Column('text_sent', sa.Boolean(), nullable=True),
    sa.Column('email_sent', sa.Boolean(), nullable=True),
    sa.Column('followup_needed', sa.Boolean(), nullable=True),
    sa.Column('next_followup_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('remarks', sa.Text(), server_default='', nullable=False),
    sa.Column('first_committed_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('edit_deadline', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('lock_window_minutes', sa.SmallInteger(), nullable=True),
    sa.Column('conflict', sa.String(length=10), nullable=True),
    sa.Column('deleted_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('deleted_by', sa.UUID(), nullable=True),
    sa.Column('delete_reason', sa.Text(), nullable=True),
    sa.Column('domain_id', sa.String(length=60), nullable=True),
    sa.Column('project_id', sa.String(length=5), nullable=True),
    sa.Column('score_points', sa.SmallInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('legacy_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.ForeignKeyConstraint(['deleted_by'], ['users.id'], name=op.f('fk_activities_deleted_by_users')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_activities_org_id_orgs')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_activities_owner_id_users')),
    sa.PrimaryKeyConstraint('id', 'business_date', name=op.f('pk_activities')),
    postgresql_partition_by='RANGE (business_date)'
    )
    op.create_index('ix_activities_created_brin', 'activities', ['created_at'], unique=False, postgresql_using='brin')
    op.create_index('ix_activities_email', 'activities', ['org_id', 'email_key'], unique=False)
    op.create_index('ix_activities_legacy', 'activities', ['legacy_id'], unique=False)
    op.create_index('ix_activities_owner_date', 'activities', ['org_id', 'owner_id', 'business_date'], unique=False)
    op.create_index('ix_activities_phone', 'activities', ['org_id', 'phone_key'], unique=False)
    op.create_index('ix_activities_prospect', 'activities', ['prospect_id'], unique=False)
    op.create_table('activity_revisions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('activity_id', sa.UUID(), nullable=False),
    sa.Column('business_date', sa.Date(), nullable=False),
    sa.Column('rev', sa.Integer(), nullable=False),
    sa.Column('changed_by', sa.UUID(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('before', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('after', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['changed_by'], ['users.id'], name=op.f('fk_activity_revisions_changed_by_users')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_activity_revisions_org_id_orgs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_activity_revisions')),
    sa.UniqueConstraint('activity_id', 'rev', name=op.f('uq_activity_revisions_activity_id_rev'))
    )
    op.create_table('agent_day_stats',
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('business_date', sa.Date(), nullable=False),
    sa.Column('team_id', sa.UUID(), nullable=True),
    sa.Column('domain_id', sa.String(length=60), nullable=True),
    sa.Column('project_id', sa.String(length=5), nullable=True),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('connected', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('sms', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('emails', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('any_touch', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('research', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('no_call', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('points', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('points_event', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('followups_created', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('strong_requested', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('strong_confirmed', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('leads', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('signups', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('duplicates', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('red_flags', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('post_lock_edits', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_activity_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_agent_day_stats_org_id_orgs')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_agent_day_stats_user_id_users')),
    sa.PrimaryKeyConstraint('org_id', 'user_id', 'business_date', name=op.f('pk_agent_day_stats'))
    )
    op.create_index('ix_agent_day_stats_date', 'agent_day_stats', ['org_id', 'business_date'], unique=False)
    op.create_table('agent_hour_stats',
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('business_date', sa.Date(), nullable=False),
    sa.Column('hour', sa.SmallInteger(), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('connected', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('points', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_agent_hour_stats_org_id_orgs')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_agent_hour_stats_user_id_users')),
    sa.PrimaryKeyConstraint('org_id', 'user_id', 'business_date', 'hour', name=op.f('pk_agent_hour_stats'))
    )
    op.create_table('contact_points',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('prospect_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('value_display', sa.Text(), nullable=False),
    sa.Column('value_key', sa.String(length=64), nullable=False),
    sa.Column('extension', sa.String(length=10), nullable=True),
    sa.Column('label', sa.String(length=40), nullable=True),
    sa.Column('is_shared_line', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_contact_points_org_id_orgs')),
    sa.ForeignKeyConstraint(['prospect_id'], ['prospects.id'], name=op.f('fk_contact_points_prospect_id_prospects')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_contact_points'))
    )
    op.create_index('ix_contact_points_key', 'contact_points', ['org_id', 'kind', 'value_key'], unique=False)
    op.create_table('dashboard_layouts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('template_name', sa.String(length=80), nullable=True),
    sa.Column('layout', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_dashboard_layouts_org_id_orgs')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_dashboard_layouts_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dashboard_layouts')),
    sa.UniqueConstraint('user_id', name=op.f('uq_dashboard_layouts_user_id'))
    )
    op.create_table('exports',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('requested_by', sa.UUID(), nullable=False),
    sa.Column('approved_by', sa.UUID(), nullable=True),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('filters', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='requested', nullable=False),
    sa.Column('object_key', sa.String(length=300), nullable=True),
    sa.Column('watermark', sa.String(length=200), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], name=op.f('fk_exports_approved_by_users')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_exports_org_id_orgs')),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], name=op.f('fk_exports_requested_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_exports'))
    )
    op.create_table('flags',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('severity', sa.String(length=8), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('phone_key', sa.String(length=64), nullable=True),
    sa.Column('activity_id', sa.UUID(), nullable=True),
    sa.Column('other_activity_id', sa.UUID(), nullable=True),
    sa.Column('lead_id', sa.UUID(), nullable=True),
    sa.Column('raised_for_user', sa.UUID(), nullable=True),
    sa.Column('other_user', sa.UUID(), nullable=True),
    sa.Column('assigned_to_supervisor', sa.UUID(), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('status', sa.String(length=10), server_default='open', nullable=False),
    sa.Column('resolution', sa.String(length=30), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('resolved_by', sa.UUID(), nullable=True),
    sa.Column('resolved_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('escalated_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("severity IN ('info', 'amber', 'red')", name=op.f('ck_flags_severity')),
    sa.CheckConstraint("status IN ('open', 'ack', 'resolved')", name=op.f('ck_flags_status')),
    sa.ForeignKeyConstraint(['assigned_to_supervisor'], ['users.id'], name=op.f('fk_flags_assigned_to_supervisor_users')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_flags_org_id_orgs')),
    sa.ForeignKeyConstraint(['other_user'], ['users.id'], name=op.f('fk_flags_other_user_users')),
    sa.ForeignKeyConstraint(['raised_for_user'], ['users.id'], name=op.f('fk_flags_raised_for_user_users')),
    sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], name=op.f('fk_flags_resolved_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_flags'))
    )
    op.create_index('ix_flags_open', 'flags', ['org_id', 'status', 'severity', 'created_at'], unique=False)
    op.create_table('google_connections',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('encrypted_refresh_token', sa.LargeBinary(), nullable=False),
    sa.Column('calendar_id', sa.String(length=200), server_default='primary', nullable=False),
    sa.Column('sync_token', sa.Text(), nullable=True),
    sa.Column('channel_id', sa.String(length=100), nullable=True),
    sa.Column('channel_expires_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_google_connections_org_id_orgs')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_google_connections_user_id_users')),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_google_connections'))
    )
    op.create_table('import_batches',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=20), server_default='paste', nullable=False),
    sa.Column('request_key', sa.String(length=80), nullable=True),
    sa.Column('body_hash', sa.String(length=64), nullable=True),
    sa.Column('file_object_key', sa.String(length=300), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('sheet', sa.String(length=120), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
    sa.Column('totals', postgresql.JSONB(astext_type=Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('mapping', postgresql.JSONB(astext_type=Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_import_batches_created_by_users')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_import_batches_org_id_orgs')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_import_batches_owner_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_import_batches')),
    sa.UniqueConstraint('owner_id', 'request_key', name=op.f('uq_import_batches_owner_id_request_key'))
    )
    op.create_table('leads',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('prospect_id', sa.UUID(), nullable=True),
    sa.Column('origin_activity_id', sa.UUID(), nullable=False),
    sa.Column('origin_activity_date', sa.Date(), nullable=False),
    sa.Column('phone_key', sa.String(length=64), nullable=True),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('supervisor_id', sa.UUID(), nullable=True),
    sa.Column('stage', sa.String(length=30), server_default='qualified_lead', nullable=False),
    sa.Column('stage_changed_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('qualified_by', sa.UUID(), nullable=True),
    sa.Column('qualified_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('signed_by', sa.UUID(), nullable=True),
    sa.Column('signed_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('lost_reason', sa.Text(), nullable=True),
    sa.Column('domain_id', sa.String(length=60), nullable=True),
    sa.Column('project_id', sa.String(length=5), nullable=True),
    sa.Column('agreement_locked_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('agreement_status', sa.String(length=30), nullable=True),
    sa.Column('legacy_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("stage IN ('qualified_lead', 'meeting', 'signed_up', 'agreement_in_progress', 'client', 'lost')", name=op.f('ck_leads_stage')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_leads_org_id_orgs')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_leads_owner_id_users')),
    sa.ForeignKeyConstraint(['prospect_id'], ['prospects.id'], name=op.f('fk_leads_prospect_id_prospects')),
    sa.ForeignKeyConstraint(['qualified_by'], ['users.id'], name=op.f('fk_leads_qualified_by_users')),
    sa.ForeignKeyConstraint(['signed_by'], ['users.id'], name=op.f('fk_leads_signed_by_users')),
    sa.ForeignKeyConstraint(['supervisor_id'], ['users.id'], name=op.f('fk_leads_supervisor_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leads')),
    sa.UniqueConstraint('legacy_id', name=op.f('uq_leads_legacy_id'))
    )
    op.create_index('ix_leads_owner_stage', 'leads', ['org_id', 'owner_id', 'stage'], unique=False)
    op.create_table('module_grants',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('module', sa.String(length=20), nullable=False),
    sa.Column('role', sa.String(length=30), nullable=False),
    sa.Column('companies', postgresql.JSONB(astext_type=Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('parent_admin_ref', sa.String(length=64), nullable=True),
    sa.Column('external_account_id', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='active', nullable=False),
    sa.Column('granted_by', sa.UUID(), nullable=True),
    sa.Column('granted_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revoked_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.CheckConstraint("module IN ('sales', 'agreements')", name=op.f('ck_module_grants_module')),
    sa.ForeignKeyConstraint(['granted_by'], ['users.id'], name=op.f('fk_module_grants_granted_by_users')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_module_grants_org_id_orgs')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_module_grants_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_module_grants'))
    )
    op.create_index('ix_module_grants_user', 'module_grants', ['user_id', 'module'], unique=False)
    op.create_table('notifications',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('read_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_notifications_org_id_orgs')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notifications_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications'))
    )
    op.create_index('ix_notifications_user', 'notifications', ['user_id', 'read_at', 'created_at'], unique=False)
    op.create_table('oidc_identities',
    sa.Column('issuer', sa.String(length=500), nullable=False),
    sa.Column('subject', sa.String(length=250), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_oidc_identities_user_id_users')),
    sa.PrimaryKeyConstraint('issuer', 'subject', name=op.f('pk_oidc_identities'))
    )
    op.create_table('phone_registry',
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('phone_key', sa.String(length=64), nullable=False),
    sa.Column('first_seen_activity_id', sa.UUID(), nullable=True),
    sa.Column('first_seen_by', sa.UUID(), nullable=True),
    sa.Column('first_seen_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('first_attempt_activity_id', sa.UUID(), nullable=True),
    sa.Column('first_attempt_by', sa.UUID(), nullable=True),
    sa.Column('first_attempt_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('first_connect_activity_id', sa.UUID(), nullable=True),
    sa.Column('first_connect_by', sa.UUID(), nullable=True),
    sa.Column('first_connect_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('state', sa.String(length=20), server_default='none', nullable=False),
    sa.Column('state_owner_id', sa.UUID(), nullable=True),
    sa.Column('protected_lead_id', sa.UUID(), nullable=True),
    sa.Column('protected_owner_id', sa.UUID(), nullable=True),
    sa.Column('last_activity_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("state IN ('none', 'followup', 'strong_pending', 'strong_confirmed', 'lead', 'meeting', 'signed', 'client')", name=op.f('ck_phone_registry_state')),
    sa.ForeignKeyConstraint(['first_attempt_by'], ['users.id'], name=op.f('fk_phone_registry_first_attempt_by_users')),
    sa.ForeignKeyConstraint(['first_connect_by'], ['users.id'], name=op.f('fk_phone_registry_first_connect_by_users')),
    sa.ForeignKeyConstraint(['first_seen_by'], ['users.id'], name=op.f('fk_phone_registry_first_seen_by_users')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_phone_registry_org_id_orgs')),
    sa.ForeignKeyConstraint(['protected_owner_id'], ['users.id'], name=op.f('fk_phone_registry_protected_owner_id_users')),
    sa.ForeignKeyConstraint(['state_owner_id'], ['users.id'], name=op.f('fk_phone_registry_state_owner_id_users')),
    sa.PrimaryKeyConstraint('org_id', 'phone_key', name=op.f('pk_phone_registry'))
    )
    op.create_table('score_ledger',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('activity_id', sa.UUID(), nullable=False),
    sa.Column('activity_date', sa.Date(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('business_date', sa.Date(), nullable=False),
    sa.Column('delta_points', sa.SmallInteger(), nullable=False),
    sa.Column('reason', sa.String(length=12), nullable=False),
    sa.Column('rule_version', sa.String(length=20), server_default='score.v1', nullable=False),
    sa.Column('at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_score_ledger_org_id_orgs')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_score_ledger_user_id_users')),
    sa.PrimaryKeyConstraint('id', 'business_date', name=op.f('pk_score_ledger')),
    postgresql_partition_by='RANGE (business_date)'
    )
    op.create_index('ix_score_ledger_activity', 'score_ledger', ['activity_id'], unique=False)
    op.create_index('ix_score_ledger_user_date', 'score_ledger', ['org_id', 'user_id', 'business_date'], unique=False)
    op.create_table('settings',
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_settings_org_id_orgs')),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_settings_updated_by_users')),
    sa.PrimaryKeyConstraint('org_id', 'key', name=op.f('pk_settings'))
    )
    op.create_table('strong_followups',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('prospect_id', sa.UUID(), nullable=True),
    sa.Column('phone_key', sa.String(length=64), nullable=True),
    sa.Column('activity_id', sa.UUID(), nullable=False),
    sa.Column('activity_date', sa.Date(), nullable=False),
    sa.Column('requested_by', sa.UUID(), nullable=False),
    sa.Column('requested_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('supervisor_id', sa.UUID(), nullable=True),
    sa.Column('decision', sa.String(length=12), server_default='pending', nullable=False),
    sa.Column('decided_by', sa.UUID(), nullable=True),
    sa.Column('decided_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('target_window', sa.String(length=80), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("decision IN ('pending', 'confirmed', 'rejected')", name=op.f('ck_strong_followups_decision')),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_strong_followups_decided_by_users')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_strong_followups_org_id_orgs')),
    sa.ForeignKeyConstraint(['prospect_id'], ['prospects.id'], name=op.f('fk_strong_followups_prospect_id_prospects')),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], name=op.f('fk_strong_followups_requested_by_users')),
    sa.ForeignKeyConstraint(['supervisor_id'], ['users.id'], name=op.f('fk_strong_followups_supervisor_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_strong_followups'))
    )
    op.create_table('targets',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('effective_date', sa.Date(), nullable=False),
    sa.Column('call_target', sa.Integer(), nullable=False),
    sa.Column('conn_target', sa.Numeric(precision=5, scale=4), nullable=False),
    sa.Column('quality_target', sa.Numeric(precision=5, scale=4), nullable=True),
    sa.Column('bands', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('set_by', sa.UUID(), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_targets_org_id_orgs')),
    sa.ForeignKeyConstraint(['set_by'], ['users.id'], name=op.f('fk_targets_set_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_targets')),
    sa.UniqueConstraint('org_id', 'effective_date', name=op.f('uq_targets_org_id_effective_date'))
    )
    op.create_table('teams',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('kind', sa.String(length=10), server_default='team', nullable=False),
    sa.Column('supervisor_id', sa.UUID(), nullable=False),
    sa.Column('leader_id', sa.UUID(), nullable=True),
    sa.Column('parent_team_id', sa.UUID(), nullable=True),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('legacy_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("kind IN ('team', 'group')", name=op.f('ck_teams_kind')),
    sa.ForeignKeyConstraint(['leader_id'], ['users.id'], name=op.f('fk_teams_leader_id_users')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_teams_org_id_orgs')),
    sa.ForeignKeyConstraint(['parent_team_id'], ['teams.id'], name=op.f('fk_teams_parent_team_id_teams')),
    sa.ForeignKeyConstraint(['supervisor_id'], ['users.id'], name=op.f('fk_teams_supervisor_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_teams')),
    sa.UniqueConstraint('legacy_id', name=op.f('uq_teams_legacy_id'))
    )
    op.create_table('user_domain_assignments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('domain_id', sa.String(length=60), nullable=False),
    sa.Column('project_id', sa.String(length=5), nullable=True),
    sa.Column('effective_from', sa.Date(), nullable=False),
    sa.Column('effective_to', sa.Date(), nullable=True),
    sa.Column('set_by', sa.UUID(), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], name=op.f('fk_user_domain_assignments_domain_id_domains')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_user_domain_assignments_org_id_orgs')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_user_domain_assignments_project_id_projects')),
    sa.ForeignKeyConstraint(['set_by'], ['users.id'], name=op.f('fk_user_domain_assignments_set_by_users')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_domain_assignments_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_domain_assignments'))
    )
    op.create_index('ix_user_domain_assignments_user', 'user_domain_assignments', ['user_id', 'effective_from'], unique=False)
    op.create_table('user_leave',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=20), server_default='leave', nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_leave_user_id_users')),
    sa.PrimaryKeyConstraint('user_id', 'date', name=op.f('pk_user_leave'))
    )
    op.create_table('user_sessions',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('csrf', sa.String(length=64), nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', postgresql.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('idle_expires_at', postgresql.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('ip', postgresql.INET(), nullable=True),
    sa.Column('user_agent', sa.String(length=300), nullable=True),
    sa.Column('revoked_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_sessions_user_id_users')),
    sa.PrimaryKeyConstraint('token_hash', name=op.f('pk_user_sessions'))
    )
    op.create_index('ix_user_sessions_user', 'user_sessions', ['user_id'], unique=False)
    op.create_table('attribution_ledger',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('lead_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('share', sa.Numeric(precision=5, scale=4), server_default=sa.text('0'), nullable=False),
    sa.Column('source_activity_id', sa.UUID(), nullable=True),
    sa.Column('at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('first_seen', 'first_attempt', 'first_connect', 'nurture', 'qualifier', 'closer', 'supervisor')", name=op.f('ck_attribution_ledger_role')),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_attribution_ledger_lead_id_leads')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_attribution_ledger_org_id_orgs')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_attribution_ledger_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_attribution_ledger'))
    )
    op.create_index('ix_attribution_ledger_lead', 'attribution_ledger', ['lead_id'], unique=False)
    op.create_table('celebrations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('lead_id', sa.UUID(), nullable=False),
    sa.Column('agent_id', sa.UUID(), nullable=False),
    sa.Column('supervisor_id', sa.UUID(), nullable=True),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['users.id'], name=op.f('fk_celebrations_agent_id_users')),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_celebrations_lead_id_leads')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_celebrations_org_id_orgs')),
    sa.ForeignKeyConstraint(['supervisor_id'], ['users.id'], name=op.f('fk_celebrations_supervisor_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_celebrations'))
    )
    op.create_table('clients',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('prospect_id', sa.UUID(), nullable=True),
    sa.Column('lead_id', sa.UUID(), nullable=True),
    sa.Column('display_name', sa.String(length=200), nullable=False),
    sa.Column('legal_name', sa.String(length=200), nullable=True),
    sa.Column('domain_id', sa.String(length=60), nullable=True),
    sa.Column('project_id', sa.String(length=5), nullable=True),
    sa.Column('tfsc_client_id', sa.String(length=64), nullable=True),
    sa.Column('agreement_status', sa.String(length=30), nullable=True),
    sa.Column('primary_contact', postgresql.JSONB(astext_type=Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('address', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('account_owner_id', sa.UUID(), nullable=True),
    sa.Column('supervisor_id', sa.UUID(), nullable=True),
    sa.Column('sale_date', sa.Date(), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.ForeignKeyConstraint(['account_owner_id'], ['users.id'], name=op.f('fk_clients_account_owner_id_users')),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_clients_lead_id_leads')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_clients_org_id_orgs')),
    sa.ForeignKeyConstraint(['prospect_id'], ['prospects.id'], name=op.f('fk_clients_prospect_id_prospects')),
    sa.ForeignKeyConstraint(['supervisor_id'], ['users.id'], name=op.f('fk_clients_supervisor_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_clients')),
    sa.UniqueConstraint('lead_id', name=op.f('uq_clients_lead_id'))
    )
    op.create_table('followups',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('activity_id', sa.UUID(), nullable=True),
    sa.Column('activity_date', sa.Date(), nullable=True),
    sa.Column('prospect_id', sa.UUID(), nullable=True),
    sa.Column('phone_key', sa.String(length=64), nullable=True),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('due_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=12), server_default='open', nullable=False),
    sa.Column('outcome_note', sa.Text(), nullable=True),
    sa.Column('delegated_group_id', sa.UUID(), nullable=True),
    sa.Column('completed_by', sa.UUID(), nullable=True),
    sa.Column('completed_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("status IN ('open', 'done', 'missed', 'cancelled')", name=op.f('ck_followups_status')),
    sa.ForeignKeyConstraint(['completed_by'], ['users.id'], name=op.f('fk_followups_completed_by_users')),
    sa.ForeignKeyConstraint(['delegated_group_id'], ['teams.id'], name=op.f('fk_followups_delegated_group_id_teams')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_followups_org_id_orgs')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_followups_owner_id_users')),
    sa.ForeignKeyConstraint(['prospect_id'], ['prospects.id'], name=op.f('fk_followups_prospect_id_prospects')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_followups'))
    )
    op.create_index('ix_followups_owner_due', 'followups', ['org_id', 'owner_id', 'status', 'due_at'], unique=False)
    op.create_table('import_rows',
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('batch_id', sa.UUID(), nullable=False),
    sa.Column('row_no', sa.Integer(), nullable=False),
    sa.Column('raw', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('refined', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('issues', postgresql.JSONB(astext_type=Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('decision', sa.String(length=20), server_default='pending', nullable=False),
    sa.Column('activity_id', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['batch_id'], ['import_batches.id'], name=op.f('fk_import_rows_batch_id_import_batches')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_import_rows_org_id_orgs')),
    sa.PrimaryKeyConstraint('batch_id', 'row_no', name=op.f('pk_import_rows'))
    )
    op.create_table('lead_assignments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('lead_id', sa.UUID(), nullable=False),
    sa.Column('from_user', sa.UUID(), nullable=True),
    sa.Column('to_user', sa.UUID(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('incentive_split', postgresql.JSONB(astext_type=Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('by_user', sa.UUID(), nullable=False),
    sa.Column('at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['by_user'], ['users.id'], name=op.f('fk_lead_assignments_by_user_users')),
    sa.ForeignKeyConstraint(['from_user'], ['users.id'], name=op.f('fk_lead_assignments_from_user_users')),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_lead_assignments_lead_id_leads')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_lead_assignments_org_id_orgs')),
    sa.ForeignKeyConstraint(['to_user'], ['users.id'], name=op.f('fk_lead_assignments_to_user_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_lead_assignments'))
    )
    op.create_table('lead_ownership_claims',
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('phone_key', sa.String(length=64), nullable=False),
    sa.Column('lead_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('claimed_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_lead_ownership_claims_lead_id_leads')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_lead_ownership_claims_org_id_orgs')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_lead_ownership_claims_owner_id_users')),
    sa.PrimaryKeyConstraint('org_id', 'phone_key', name=op.f('pk_lead_ownership_claims')),
    sa.UniqueConstraint('lead_id', name=op.f('uq_lead_ownership_claims_lead_id'))
    )
    op.create_table('meetings',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('lead_id', sa.UUID(), nullable=False),
    sa.Column('organizer_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('starts_at', postgresql.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('ends_at', postgresql.TIMESTAMP(timezone=True), nullable=False),
    sa.Column('timezone', sa.String(length=64), server_default='America/Los_Angeles', nullable=False),
    sa.Column('google_event_id', sa.String(length=200), nullable=True),
    sa.Column('meet_url', sa.Text(), nullable=True),
    sa.Column('attendees', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=15), server_default='scheduled', nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('legacy_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("status IN ('scheduled', 'held', 'no_show', 'rescheduled', 'cancelled')", name=op.f('ck_meetings_status')),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_meetings_lead_id_leads')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_meetings_org_id_orgs')),
    sa.ForeignKeyConstraint(['organizer_id'], ['users.id'], name=op.f('fk_meetings_organizer_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_meetings')),
    sa.UniqueConstraint('legacy_id', name=op.f('uq_meetings_legacy_id'))
    )
    op.create_table('strong_followup_comments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('strong_followup_id', sa.UUID(), nullable=False),
    sa.Column('author_id', sa.UUID(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_strong_followup_comments_author_id_users')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_strong_followup_comments_org_id_orgs')),
    sa.ForeignKeyConstraint(['strong_followup_id'], ['strong_followups.id'], name=op.f('fk_strong_followup_comments_strong_followup_id_strong_followups')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_strong_followup_comments'))
    )
    op.create_table('team_memberships',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('team_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=False),
    sa.Column('effective_to', sa.Date(), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_team_memberships_org_id_orgs')),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_team_memberships_team_id_teams')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_team_memberships_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_team_memberships'))
    )
    op.create_index('ix_team_memberships_team', 'team_memberships', ['team_id', 'effective_from'], unique=False)
    op.create_index('ix_team_memberships_user', 'team_memberships', ['user_id', 'effective_from'], unique=False)
    op.create_table('followup_notes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('followup_id', sa.UUID(), nullable=False),
    sa.Column('author_id', sa.UUID(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_followup_notes_author_id_users')),
    sa.ForeignKeyConstraint(['followup_id'], ['followups.id'], name=op.f('fk_followup_notes_followup_id_followups')),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_followup_notes_org_id_orgs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_followup_notes'))
    )
    op.create_foreign_key(op.f('fk_projects_owner_user_id_users'), 'projects', 'users', ['owner_user_id'], ['id'])
    op.create_index('ix_prospects_name_trgm', 'prospects', ['name_key'], postgresql_using='gin', postgresql_ops={'name_key': 'gin_trgm_ops'})
    op.execute(PARTITION_FN)
    op.execute(PARTITION_FOR_FN)
    op.execute('SELECT crm_ensure_partitions(2, 3)')
    # Row-Level Security: the API role sees only rows of app.org_id. The table owner
    # (migration role) bypasses RLS unless FORCE is set; the app connects as crm_app.
    for name in _rls_tables():
        op.execute(f'ALTER TABLE {name} ENABLE ROW LEVEL SECURITY')
        op.execute(f"CREATE POLICY org_isolation ON {name} USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid) "
                   f"WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)")
    op.execute("""
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'crm_app') THEN
    CREATE ROLE crm_app NOLOGIN;
  END IF;
END $$;
""")
    op.execute('GRANT USAGE ON SCHEMA public TO crm_app')
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO crm_app')
    op.execute('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO crm_app')
    op.execute('GRANT EXECUTE ON FUNCTION crm_ensure_partitions(int, int), crm_ensure_partition_for(text, date) TO crm_app')
    op.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO crm_app')
    # audit_log is append-only for the application role (tamper evidence, §21)
    op.execute('REVOKE UPDATE, DELETE ON audit_log, activity_revisions, lead_assignments, score_ledger FROM crm_app')


def downgrade() -> None:
    for name in _rls_tables():
        op.execute(f'DROP POLICY IF EXISTS org_isolation ON {name}')
    op.drop_constraint(op.f('fk_projects_owner_user_id_users'), 'projects', type_='foreignkey')
    op.drop_index('ix_prospects_name_trgm', table_name='prospects')
    op.drop_table('followup_notes')
    op.drop_index('ix_team_memberships_user', table_name='team_memberships')
    op.drop_index('ix_team_memberships_team', table_name='team_memberships')
    op.drop_table('team_memberships')
    op.drop_table('strong_followup_comments')
    op.drop_table('meetings')
    op.drop_table('lead_ownership_claims')
    op.drop_table('lead_assignments')
    op.drop_table('import_rows')
    op.drop_index('ix_followups_owner_due', table_name='followups')
    op.drop_table('followups')
    op.drop_table('clients')
    op.drop_table('celebrations')
    op.drop_index('ix_attribution_ledger_lead', table_name='attribution_ledger')
    op.drop_table('attribution_ledger')
    op.drop_index('ix_user_sessions_user', table_name='user_sessions')
    op.drop_table('user_sessions')
    op.drop_table('user_leave')
    op.drop_index('ix_user_domain_assignments_user', table_name='user_domain_assignments')
    op.drop_table('user_domain_assignments')
    op.drop_table('teams')
    op.drop_table('targets')
    op.drop_table('strong_followups')
    op.drop_table('settings')
    op.drop_index('ix_score_ledger_user_date', table_name='score_ledger')
    op.drop_index('ix_score_ledger_activity', table_name='score_ledger')
    op.drop_table('score_ledger')
    op.drop_table('phone_registry')
    op.drop_table('oidc_identities')
    op.drop_index('ix_notifications_user', table_name='notifications')
    op.drop_table('notifications')
    op.drop_index('ix_module_grants_user', table_name='module_grants')
    op.drop_table('module_grants')
    op.drop_index('ix_leads_owner_stage', table_name='leads')
    op.drop_table('leads')
    op.drop_table('import_batches')
    op.drop_table('google_connections')
    op.drop_index('ix_flags_open', table_name='flags')
    op.drop_table('flags')
    op.drop_table('exports')
    op.drop_table('dashboard_layouts')
    op.drop_index('ix_contact_points_key', table_name='contact_points')
    op.drop_table('contact_points')
    op.drop_table('agent_hour_stats')
    op.drop_index('ix_agent_day_stats_date', table_name='agent_day_stats')
    op.drop_table('agent_day_stats')
    op.drop_table('activity_revisions')
    op.drop_index('ix_activities_prospect', table_name='activities')
    op.drop_index('ix_activities_phone', table_name='activities')
    op.drop_index('ix_activities_owner_date', table_name='activities')
    op.drop_index('ix_activities_legacy', table_name='activities')
    op.drop_index('ix_activities_email', table_name='activities')
    op.drop_index('ix_activities_created_brin', table_name='activities', postgresql_using='brin')
    op.drop_table('activities')
    op.drop_index('ix_users_org_manager', table_name='users')
    op.drop_table('users')
    op.drop_table('prospects')
    op.drop_table('projects')
    op.drop_table('work_calendar')
    op.drop_index('ix_outbox_due', table_name='outbox')
    op.drop_table('outbox')
    op.drop_table('domains')
    op.drop_index('ix_data_access_log_actor', table_name='data_access_log')
    op.drop_table('data_access_log')
    op.drop_index('ix_audit_log_resource', table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_table('orgs')
    op.drop_table('inbox')
    op.execute('DROP FUNCTION IF EXISTS crm_ensure_partitions(int, int)')
    op.execute('DROP FUNCTION IF EXISTS crm_ensure_partition_for(text, date)')
    op.execute('REVOKE ALL ON SCHEMA public FROM crm_app')
    op.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM crm_app')

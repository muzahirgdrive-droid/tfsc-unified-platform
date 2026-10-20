"""Pilot v1.0.1 baseline: the exact schema the pilot's create_all produced.

Existing pilot databases are adopted with `alembic stamp 0001_pilot_baseline`
(never re-created). Fresh databases run it normally. Generated once from the
pilot metadata and frozen.

Revision ID: 0001_pilot_baseline
Revises:
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = '0001_pilot_baseline'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('crm_domains',
    sa.Column('id', sa.String(length=60), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('crm_outbox',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.String(length=32), nullable=False),
    sa.Column('owner_id', sa.String(length=32), nullable=False),
    sa.Column('event_type', sa.String(length=80), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('state', sa.String(length=30), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('available_at', sa.Float(), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_crm_outbox_delivery', 'crm_outbox', ['state', 'available_at'], unique=False)
    op.create_index(op.f('ix_crm_outbox_org_id'), 'crm_outbox', ['org_id'], unique=False)
    op.create_table('crm_settings',
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('value', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('crm_target_versions',
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('effective_date', sa.String(length=10), nullable=False),
    sa.Column('call_target', sa.Integer(), nullable=False),
    sa.Column('connection_target', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('version')
    )
    op.create_index(op.f('ix_crm_target_versions_effective_date'), 'crm_target_versions', ['effective_date'], unique=False)
    op.create_table('crm_throttle',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.Column('until', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('crm_users',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.String(length=32), nullable=False),
    sa.Column('email', sa.String(length=200), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('password', sa.Text(), nullable=False),
    sa.Column('role', sa.String(length=24), nullable=False),
    sa.Column('manager_id', sa.String(length=32), nullable=True),
    sa.Column('agent_id', sa.String(length=80), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('routing_view', sa.Boolean(), nullable=False),
    sa.Column('domain_id', sa.String(length=60), nullable=False),
    sa.Column('project_id', sa.String(length=32), nullable=True),
    sa.Column('agreements_user_id', sa.String(length=32), nullable=True),
    sa.Column('created', sa.Float(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('photo', sa.Text(), nullable=True),
    sa.Column('reset_required', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['manager_id'], ['crm_users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_crm_users_org_id'), 'crm_users', ['org_id'], unique=False)
    op.create_table('oidc_states',
    sa.Column('state', sa.String(length=64), nullable=False),
    sa.Column('verifier', sa.String(length=100), nullable=False),
    sa.Column('nonce', sa.String(length=100), nullable=False),
    sa.Column('expires', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('state')
    )
    op.create_table('crm_activities',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.String(length=32), nullable=False),
    sa.Column('owner_id', sa.String(length=32), nullable=False),
    sa.Column('provider_name', sa.String(length=200), nullable=False),
    sa.Column('phone', sa.String(length=100), nullable=True),
    sa.Column('phone_key', sa.String(length=32), nullable=True),
    sa.Column('extension', sa.String(length=20), nullable=True),
    sa.Column('email', sa.String(length=250), nullable=True),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('business_date', sa.String(length=10), nullable=False),
    sa.Column('called', sa.Boolean(), nullable=True),
    sa.Column('connected', sa.Boolean(), nullable=True),
    sa.Column('text_sent', sa.Boolean(), nullable=True),
    sa.Column('email_sent', sa.Boolean(), nullable=True),
    sa.Column('next_followup', sa.String(length=40), nullable=True),
    sa.Column('remarks', sa.Text(), nullable=False),
    sa.Column('raw', sa.JSON(), nullable=False),
    sa.Column('source', sa.String(length=30), nullable=False),
    sa.Column('domain_id', sa.String(length=60), nullable=False),
    sa.Column('project_id', sa.String(length=32), nullable=True),
    sa.Column('created', sa.Float(), nullable=False),
    sa.Column('updated', sa.Float(), nullable=False),
    sa.Column('first_committed_at', sa.Float(), nullable=True),
    sa.Column('edit_deadline', sa.Float(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('deleted', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['crm_users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_crm_activities_org_id'), 'crm_activities', ['org_id'], unique=False)
    op.create_index(op.f('ix_crm_activities_phone_key'), 'crm_activities', ['phone_key'], unique=False)
    op.create_index('ix_crm_activity_followup', 'crm_activities', ['owner_id', 'next_followup'], unique=False)
    op.create_index('ix_crm_activity_owner_date', 'crm_activities', ['org_id', 'owner_id', 'business_date', 'id'], unique=False)
    op.create_table('crm_audit',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.String(length=32), nullable=False),
    sa.Column('actor_id', sa.String(length=32), nullable=False),
    sa.Column('actor_name', sa.String(length=150), nullable=False),
    sa.Column('owner_id', sa.String(length=32), nullable=False),
    sa.Column('resource_id', sa.String(length=64), nullable=True),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('detail', sa.JSON(), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['crm_users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_crm_audit_org_id'), 'crm_audit', ['org_id'], unique=False)
    op.create_index('ix_crm_audit_owner_created', 'crm_audit', ['org_id', 'owner_id', 'created', 'id'], unique=False)
    op.create_table('crm_google_connections',
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('encrypted_token', sa.Text(), nullable=False),
    sa.Column('updated', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['crm_users.id'], ),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('crm_google_oauth_states',
    sa.Column('state', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('verifier', sa.String(length=100), nullable=False),
    sa.Column('expires', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['crm_users.id'], ),
    sa.PrimaryKeyConstraint('state')
    )
    op.create_table('crm_imports',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.String(length=32), nullable=False),
    sa.Column('owner_id', sa.String(length=32), nullable=False),
    sa.Column('request_key', sa.String(length=80), nullable=False),
    sa.Column('body_hash', sa.String(length=64), nullable=False),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['crm_users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'request_key', name='uq_crm_import')
    )
    op.create_index(op.f('ix_crm_imports_org_id'), 'crm_imports', ['org_id'], unique=False)
    op.create_table('crm_layouts',
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('layout', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['crm_users.id'], ),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('crm_projects',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=True),
    sa.Column('domain_id', sa.String(length=60), nullable=True),
    sa.Column('owner_name', sa.String(length=150), nullable=True),
    sa.Column('owner_email', sa.String(length=200), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['domain_id'], ['crm_domains.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('crm_sessions',
    sa.Column('token', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('csrf', sa.String(length=64), nullable=False),
    sa.Column('expires', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['crm_users.id'], ),
    sa.PrimaryKeyConstraint('token')
    )
    op.create_index(op.f('ix_crm_sessions_user_id'), 'crm_sessions', ['user_id'], unique=False)
    op.create_table('crm_teams',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('supervisor_id', sa.String(length=32), nullable=False),
    sa.Column('leader_id', sa.String(length=32), nullable=True),
    sa.Column('created', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['leader_id'], ['crm_users.id'], ),
    sa.ForeignKeyConstraint(['supervisor_id'], ['crm_users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_crm_teams_org_id'), 'crm_teams', ['org_id'], unique=False)
    op.create_table('oidc_accounts',
    sa.Column('issuer', sa.String(length=500), nullable=False),
    sa.Column('subject', sa.String(length=250), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['crm_users.id'], ),
    sa.PrimaryKeyConstraint('issuer', 'subject')
    )
    op.create_table('crm_conflicts',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('other_id', sa.String(length=32), nullable=False),
    sa.Column('severity', sa.String(length=30), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('resolved_by', sa.String(length=32), nullable=True),
    sa.ForeignKeyConstraint(['activity_id'], ['crm_activities.id'], ),
    sa.ForeignKeyConstraint(['other_id'], ['crm_activities.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_crm_conflicts_org_id'), 'crm_conflicts', ['org_id'], unique=False)
    op.create_table('crm_leads',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('owner_id', sa.String(length=32), nullable=False),
    sa.Column('stage', sa.String(length=40), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.Column('updated', sa.Float(), nullable=False),
    sa.Column('qualified_by', sa.String(length=32), nullable=True),
    sa.Column('sale_confirmed_at', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['activity_id'], ['crm_activities.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['crm_users.id'], ),
    sa.ForeignKeyConstraint(['qualified_by'], ['crm_users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('activity_id')
    )
    op.create_index('ix_crm_lead_owner_stage', 'crm_leads', ['org_id', 'owner_id', 'stage'], unique=False)
    op.create_index(op.f('ix_crm_leads_org_id'), 'crm_leads', ['org_id'], unique=False)
    op.create_table('crm_team_members',
    sa.Column('team_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['crm_teams.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['crm_users.id'], ),
    sa.PrimaryKeyConstraint('team_id', 'user_id')
    )
    op.create_table('crm_contract_snapshots',
    sa.Column('lead_id', sa.String(length=32), nullable=False),
    sa.Column('actor_id', sa.String(length=32), nullable=False),
    sa.Column('snapshot', sa.JSON(), nullable=False),
    sa.Column('updated', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['crm_users.id'], ),
    sa.ForeignKeyConstraint(['lead_id'], ['crm_leads.id'], ),
    sa.PrimaryKeyConstraint('lead_id', 'actor_id')
    )
    op.create_table('crm_followup_tasks',
    sa.Column('lead_id', sa.String(length=32), nullable=False),
    sa.Column('due_date', sa.String(length=10), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('updated_by', sa.String(length=32), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('updated', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['crm_leads.id'], ),
    sa.ForeignKeyConstraint(['updated_by'], ['crm_users.id'], ),
    sa.PrimaryKeyConstraint('lead_id')
    )
    op.create_table('crm_handoffs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.String(length=32), nullable=False),
    sa.Column('lead_id', sa.String(length=32), nullable=False),
    sa.Column('owner_id', sa.String(length=32), nullable=False),
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('body_hash', sa.String(length=64), nullable=False),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['crm_leads.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'key', name='uq_crm_handoff')
    )
    op.create_index(op.f('ix_crm_handoffs_org_id'), 'crm_handoffs', ['org_id'], unique=False)
    op.create_table('crm_meetings',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('org_id', sa.String(length=32), nullable=False),
    sa.Column('lead_id', sa.String(length=32), nullable=False),
    sa.Column('owner_id', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('starts_at', sa.String(length=50), nullable=False),
    sa.Column('ends_at', sa.String(length=50), nullable=True),
    sa.Column('timezone', sa.String(length=80), nullable=False),
    sa.Column('join_url', sa.Text(), nullable=True),
    sa.Column('provider_id', sa.String(length=200), nullable=True),
    sa.Column('status', sa.String(length=40), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['crm_leads.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['crm_users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_crm_meetings_org_id'), 'crm_meetings', ['org_id'], unique=False)
    op.create_table('crm_ownership_claims',
    sa.Column('claim_key', sa.String(length=100), nullable=False),
    sa.Column('lead_id', sa.String(length=32), nullable=False),
    sa.Column('owner_id', sa.String(length=32), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['crm_leads.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['crm_users.id'], ),
    sa.PrimaryKeyConstraint('claim_key'),
    sa.UniqueConstraint('lead_id')
    )


def downgrade() -> None:
    op.drop_table('crm_ownership_claims')
    op.drop_index(op.f('ix_crm_meetings_org_id'), table_name='crm_meetings')
    op.drop_table('crm_meetings')
    op.drop_index(op.f('ix_crm_handoffs_org_id'), table_name='crm_handoffs')
    op.drop_table('crm_handoffs')
    op.drop_table('crm_followup_tasks')
    op.drop_table('crm_contract_snapshots')
    op.drop_table('crm_team_members')
    op.drop_index(op.f('ix_crm_leads_org_id'), table_name='crm_leads')
    op.drop_index('ix_crm_lead_owner_stage', table_name='crm_leads')
    op.drop_table('crm_leads')
    op.drop_index(op.f('ix_crm_conflicts_org_id'), table_name='crm_conflicts')
    op.drop_table('crm_conflicts')
    op.drop_table('oidc_accounts')
    op.drop_index(op.f('ix_crm_teams_org_id'), table_name='crm_teams')
    op.drop_table('crm_teams')
    op.drop_index(op.f('ix_crm_sessions_user_id'), table_name='crm_sessions')
    op.drop_table('crm_sessions')
    op.drop_table('crm_projects')
    op.drop_table('crm_layouts')
    op.drop_index(op.f('ix_crm_imports_org_id'), table_name='crm_imports')
    op.drop_table('crm_imports')
    op.drop_table('crm_google_oauth_states')
    op.drop_table('crm_google_connections')
    op.drop_index('ix_crm_audit_owner_created', table_name='crm_audit')
    op.drop_index(op.f('ix_crm_audit_org_id'), table_name='crm_audit')
    op.drop_table('crm_audit')
    op.drop_index('ix_crm_activity_owner_date', table_name='crm_activities')
    op.drop_index('ix_crm_activity_followup', table_name='crm_activities')
    op.drop_index(op.f('ix_crm_activities_phone_key'), table_name='crm_activities')
    op.drop_index(op.f('ix_crm_activities_org_id'), table_name='crm_activities')
    op.drop_table('crm_activities')
    op.drop_table('oidc_states')
    op.drop_index(op.f('ix_crm_users_org_id'), table_name='crm_users')
    op.drop_table('crm_users')
    op.drop_table('crm_throttle')
    op.drop_index(op.f('ix_crm_target_versions_effective_date'), table_name='crm_target_versions')
    op.drop_table('crm_target_versions')
    op.drop_table('crm_settings')
    op.drop_index(op.f('ix_crm_outbox_org_id'), table_name='crm_outbox')
    op.drop_index('ix_crm_outbox_delivery', table_name='crm_outbox')
    op.drop_table('crm_outbox')
    op.drop_table('crm_domains')

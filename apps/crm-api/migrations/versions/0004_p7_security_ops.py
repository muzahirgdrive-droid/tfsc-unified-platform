"""P7: audit anchors, scheduler job runs, export approval columns, follow-up escalation marker.

Revision ID: 0004_p7_security_ops
Revises: 0003_seed_and_pilot_data
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0004_p7_security_ops'
down_revision = '0003_seed_and_pilot_data'
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
EXPORT_COLUMNS = [
    sa.Column('reason', sa.Text()),
    sa.Column('approved_at', TS),
    sa.Column('expires_at', TS),
    sa.Column('file_sha256', sa.String(64)),
    sa.Column('password_enc', sa.Text()),
    sa.Column('password_revealed_at', TS),
    sa.Column('downloads', sa.Integer(), nullable=False, server_default=sa.text('0')),
    sa.Column('error', sa.Text()),
]


def upgrade() -> None:
    op.add_column('followups', sa.Column('escalated_at', TS))
    for col in EXPORT_COLUMNS:
        op.add_column('exports', col)
    op.create_table(
        'audit_anchors',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('anchored_for', sa.Date(), nullable=False),
        sa.Column('last_audit_id', sa.BigInteger(), nullable=False),
        sa.Column('head_hash', sa.String(64), nullable=False),
        sa.Column('rows', sa.Integer(), nullable=False),
        sa.Column('sink', sa.String(40), nullable=False),
        sa.Column('sink_ref', sa.String(300)),
        sa.Column('created_at', TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_audit_anchors_org_id_orgs')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_anchors')),
        sa.UniqueConstraint('org_id', 'anchored_for', name=op.f('uq_audit_anchors_org_id_anchored_for')),
    )
    op.create_table(
        'job_runs',
        sa.Column('job', sa.String(60), nullable=False),
        sa.Column('period_key', sa.String(40), nullable=False),
        sa.Column('started_at', TS, nullable=False, server_default=sa.func.now()),
        sa.Column('finished_at', TS),
        sa.Column('status', sa.String(12), nullable=False, server_default='running'),
        sa.Column('detail', postgresql.JSONB()),
        sa.PrimaryKeyConstraint('job', 'period_key', name=op.f('pk_job_runs')),
    )
    op.execute('ALTER TABLE audit_anchors ENABLE ROW LEVEL SECURITY')
    op.execute("CREATE POLICY org_isolation ON audit_anchors USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid) "
               "WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)")
    op.execute('GRANT SELECT, INSERT ON audit_anchors TO crm_app')
    op.execute('GRANT USAGE, SELECT ON SEQUENCE audit_anchors_id_seq TO crm_app')
    op.execute('REVOKE UPDATE, DELETE ON audit_anchors FROM crm_app')  # anchors are write-once for the app role
    op.execute('GRANT SELECT, INSERT, UPDATE ON job_runs TO crm_app')


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS org_isolation ON audit_anchors')
    op.drop_table('job_runs')
    op.drop_table('audit_anchors')
    for col in reversed(EXPORT_COLUMNS):
        op.drop_column('exports', col.name)
    op.drop_column('followups', 'escalated_at')

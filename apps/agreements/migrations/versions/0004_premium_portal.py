"""Company settings (signatory, title, signature), saved reports, activity log details; envelopes stop expiring by default

Revision ID: 0004_premium_portal
Revises: 0003_docusign_parity
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0004_premium_portal'
down_revision = '0003_docusign_parity'
branch_labels = None
depends_on = None

OLD_DEFAULT_EXPIRY_DAYS = 30


def upgrade() -> None:
    op.create_table('company_profiles',
    sa.Column('entity', sa.String(length=100), nullable=False),
    sa.Column('signatory', sa.String(length=150), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('signature', sa.LargeBinary(), nullable=True),
    sa.Column('signature_kind', sa.String(length=10), nullable=True),
    sa.Column('updated', sa.Float(), nullable=False),
    sa.Column('updated_by', sa.String(length=32), nullable=True),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('entity')
    )
    op.create_table('saved_reports',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('owner_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('definition', sa.JSON(), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.Column('updated', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('saved_reports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_saved_reports_owner_id'), ['owner_id'], unique=False)

    with op.batch_alter_table('audit', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ip', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('user_agent', sa.String(length=300), nullable=True))
        batch_op.create_index('ix_audit_created', ['created'], unique=False)
        batch_op.create_index('ix_audit_actor_created', ['actor_id', 'created'], unique=False)

    # Signing links no longer expire unless the sender sets an expiration. Envelopes waiting for signatures that carry the
    # previous automatic 30-day default lose it; an expiration the sender chose (any other number of days) is kept.
    bind = op.get_bind()
    envelopes = sa.table('esign_envelopes', sa.column('id', sa.String), sa.column('status', sa.String), sa.column('settings', sa.JSON),
                         sa.column('expires_at', sa.Float), sa.column('expiry_warned', sa.Boolean))
    for row in bind.execute(sa.select(envelopes.c.id, envelopes.c.settings).where(envelopes.c.status.in_(('draft', 'sent', 'template')))).all():
        settings = dict(row.settings or {})
        if settings.get('expire_days', OLD_DEFAULT_EXPIRY_DAYS) == OLD_DEFAULT_EXPIRY_DAYS:
            settings['expire_days'] = 0
            bind.execute(sa.update(envelopes).where(envelopes.c.id == row.id).values(settings=settings, expires_at=None, expiry_warned=False))


def downgrade() -> None:
    with op.batch_alter_table('audit', schema=None) as batch_op:
        batch_op.drop_index('ix_audit_actor_created')
        batch_op.drop_index('ix_audit_created')
        batch_op.drop_column('user_agent')
        batch_op.drop_column('ip')

    with op.batch_alter_table('saved_reports', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_saved_reports_owner_id'))

    op.drop_table('saved_reports')
    op.drop_table('company_profiles')

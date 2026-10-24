"""Company signature size: how large each company's signature prints on its agreements

Revision ID: 0005_signature_size
Revises: 0004_premium_portal
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0005_signature_size'
down_revision = '0004_premium_portal'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Empty means the company default (entities.py). ADD COLUMN only, so SQLite never rebuilds the table.
    op.add_column('company_profiles', sa.Column('signature_scale', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('company_profiles', schema=None) as batch_op:
        batch_op.drop_column('signature_scale')

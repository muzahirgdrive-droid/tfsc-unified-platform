"""Custom contracting companies and per-user service permissions

Adds a `companies` table so a Super Admin can add contracting companies (letterhead, address,
governing law, signatory, services) from the app, and a `services` column on `users` to restrict
which services a user may put on an agreement. Both are additive, so SQLite never rebuilds a table
and existing data (users, agreements, the three built-in companies) is untouched.

Revision ID: 0006_custom_companies
Revises: 0005_signature_size
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0006_custom_companies'
down_revision = '0005_signature_size'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'companies',
        sa.Column('key', sa.String(length=40), primary_key=True),
        sa.Column('name', sa.String(length=200), nullable=False, unique=True),
        sa.Column('short', sa.String(length=120), nullable=False),
        sa.Column('display', sa.String(length=200), nullable=False),
        sa.Column('address', sa.Text(), nullable=False),
        sa.Column('email', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('governing_state', sa.String(length=60), nullable=False),
        sa.Column('timezone', sa.String(length=60), nullable=False),
        sa.Column('color', sa.String(length=9), nullable=False),
        sa.Column('accent', sa.String(length=9), nullable=False),
        sa.Column('dark', sa.String(length=9), nullable=False),
        sa.Column('signatory', sa.String(length=150), nullable=False),
        sa.Column('title', sa.String(length=150), nullable=False),
        sa.Column('signature_scale', sa.Float(), nullable=False, server_default='0.85'),
        sa.Column('letterhead', sa.LargeBinary(), nullable=False),
        sa.Column('letterhead_sha256', sa.String(length=64), nullable=False),
        sa.Column('logo', sa.LargeBinary(), nullable=True),
        sa.Column('logo_ext', sa.String(length=8), nullable=True),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('top', sa.Integer(), nullable=False),
        sa.Column('bottom', sa.Integer(), nullable=False),
        sa.Column('left', sa.Integer(), nullable=False),
        sa.Column('right', sa.Integer(), nullable=False),
        sa.Column('services', sa.JSON(), nullable=False),
        sa.Column('doc_types', sa.JSON(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created', sa.Float(), nullable=False),
        sa.Column('created_by', sa.String(length=32), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('updated', sa.Float(), nullable=False),
    )
    # Empty / NULL means every service the company offers is allowed. ADD COLUMN only.
    op.add_column('users', sa.Column('services', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('services')
    op.drop_table('companies')

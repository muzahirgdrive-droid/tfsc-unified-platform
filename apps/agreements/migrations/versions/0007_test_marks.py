"""Test agreements: mark contracts, clients and envelopes as practice runs

Adds a `test` flag (NOT NULL, default false) to `contracts`, `clients` and `esign_envelopes`. Test items are watermarked,
send "[TEST]" emails and stay out of Home, Insights, reports and client counts unless a report asks to include them.
ADD COLUMN only, so SQLite never rebuilds a table, every existing row reads as a real (false) item and envelopes that
are already out for signature keep working exactly as before.

Revision ID: 0007_test_marks
Revises: 0006_custom_companies
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0007_test_marks'
down_revision = '0006_custom_companies'
branch_labels = None
depends_on = None

TABLES = ('contracts', 'clients', 'esign_envelopes')


def upgrade() -> None:
    # Only what is missing: if an update was stepped back after this ran (agreements.sh rollback), running it again is safe.
    inspector = sa.inspect(op.get_bind())
    for table in TABLES:
        if 'test' not in {c['name'] for c in inspector.get_columns(table)}:
            op.add_column(table, sa.Column('test', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    for table in reversed(TABLES):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('test')

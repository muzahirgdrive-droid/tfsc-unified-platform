"""P8: per-team round-robin assignment (§14.4) and the lead protection review (§27).

Revision ID: 0005_assignment_protection
Revises: 0004_p7_security_ops
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0005_assignment_protection'
down_revision = '0004_p7_security_ops'
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column('teams', sa.Column('auto_assign', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('teams', sa.Column('lead_capacity', sa.Integer(), nullable=False, server_default=sa.text('25')))
    op.create_check_constraint(op.f('ck_teams_lead_capacity'), 'teams', 'lead_capacity BETWEEN 1 AND 500')
    op.add_column('leads', sa.Column('protection_renewed_at', TS))
    op.add_column('leads', sa.Column('protection_review_at', TS))


def downgrade() -> None:
    op.drop_column('leads', 'protection_review_at')
    op.drop_column('leads', 'protection_renewed_at')
    op.drop_constraint(op.f('ck_teams_lead_capacity'), 'teams', type_='check')
    op.drop_column('teams', 'lead_capacity')
    op.drop_column('teams', 'auto_assign')

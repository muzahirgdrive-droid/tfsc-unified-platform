"""P8: S3-compatible object storage for stored files (metadata stays in stored_files, bytes may live in a bucket).

Revision ID: 0006_object_storage
Revises: 0005_assignment_protection
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0006_object_storage'
down_revision = '0005_assignment_protection'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('stored_files', 'data', existing_type=sa.LargeBinary(), nullable=True)
    op.add_column('stored_files', sa.Column('backend', sa.String(10), nullable=False, server_default='db'))
    op.create_check_constraint(op.f('ck_stored_files_backend'), 'stored_files', "backend IN ('db', 's3') AND (backend = 's3' OR data IS NOT NULL)")
    op.add_column('audit_anchors', sa.Column('remote_ref', sa.String(300)))


def downgrade() -> None:
    op.drop_column('audit_anchors', 'remote_ref')
    op.drop_constraint(op.f('ck_stored_files_backend'), 'stored_files', type_='check')
    op.drop_column('stored_files', 'backend')
    op.alter_column('stored_files', 'data', existing_type=sa.LargeBinary(), nullable=False)

"""Follow-up notes, and the same side margins for every company

* followup_notes: a note someone writes when they follow up on an agreement (sent or not yet sent): when, who, how
  (phone, email, meeting, other), what happened and, if agreed, when to follow up next. A new table; nothing else changes.
* Side margins: every company's agreements use 0.65 inch on the left and right (936 twips), as Scribe Align's always
  have. The text width follows from the page width. Top and bottom margins, the letterhead artwork and the footer do not
  change. Only agreements generated from now on are affected; documents already generated, sent or signed never change.

Additive and idempotent: the table is created only when missing, and the margins are only set where they differ.

Revision ID: 0013_followups_margins
Revises: 0012_audit_fixes
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0013_followups_margins'
down_revision = '0012_audit_fixes'
branch_labels = None
depends_on = None

SIDE = 936          # twips: 0.65 inch


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if 'followup_notes' not in tables:
        op.create_table('followup_notes',
                        sa.Column('id', sa.String(length=32), nullable=False),
                        sa.Column('client_id', sa.String(length=32), sa.ForeignKey('clients.id'), nullable=True),
                        sa.Column('envelope_id', sa.String(length=32), nullable=True),
                        sa.Column('contract_id', sa.String(length=64), nullable=True),
                        sa.Column('channel', sa.String(length=20), nullable=False),
                        sa.Column('body', sa.Text(), nullable=False),
                        sa.Column('next_on', sa.String(length=10), nullable=True),
                        sa.Column('author_id', sa.String(length=32), sa.ForeignKey('users.id'), nullable=True),
                        sa.Column('author_name', sa.String(length=150), nullable=False),
                        sa.Column('created', sa.Float(), nullable=False),
                        sa.PrimaryKeyConstraint('id'))
    for name, columns in (('ix_followups_client_created', 'client_id, created'), ('ix_followups_envelope', 'envelope_id'), ('ix_followups_contract', 'contract_id')):
        bind.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS {name} ON followup_notes ({columns})')
    # companies added in the app (and the five installed by 0009)
    if 'companies' in tables:
        rows = bind.execute(sa.text('SELECT key, width, "left", "right" FROM companies')).all()
        for key, width, left, right in rows:
            if (left, right) != (SIDE, SIDE):
                bind.execute(sa.text('UPDATE companies SET width = :w, "left" = :s, "right" = :s WHERE key = :k'),
                             {'w': int(width + left + right - 2 * SIDE), 's': SIDE, 'k': key})
    # built-in companies whose margins were changed under Company settings
    import json
    settings = sa.table('settings', sa.column('key', sa.String), sa.column('value', sa.JSON))
    for key, value in bind.execute(sa.select(settings.c.key, settings.c.value).where(settings.c.key.like('company:%'))).all():
        if key.count(':') != 1 or not isinstance(value, dict):
            continue
        if all(isinstance(value.get(g), int) for g in ('width', 'left', 'right')) and (value['left'], value['right']) != (SIDE, SIDE):
            changed = dict(value, width=value['width'] + value['left'] + value['right'] - 2 * SIDE, left=SIDE, right=SIDE)
            bind.execute(settings.update().where(settings.c.key == key).values(value=changed))
    del json


def downgrade() -> None:
    op.drop_table('followup_notes')

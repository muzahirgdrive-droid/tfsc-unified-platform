"""Reminders you control, and an email is never sent twice (release 5.4)

* reminder_rules: named schedules (when, how often, how many, which days and hours, what happens after the last one). Four
  are installed: Standard, Gentle, Firm and Off. Every company uses Standard until a Super Admin chooses otherwise.
* reminder_optouts: the do-not-remind list (a person, for every company or one, with a reason and an optional end date).
* esign_recipients: per person on an envelope, reminders switched off (and by whom), paused until, the point the schedule
  counts from after "skip the next reminder", and when the sender was told the last reminder went unanswered.
* mail_outbox.handed_at: when an email's content was handed to the mail server. Such an email is never sent again by
  itself (the cause of the repeated emails); without a confirmation it waits under "Needs a look".
* settings: reminders:installed_at (envelopes that reached their last reminder before this release do not all notify
  their senders on the first day).

Additive and idempotent: tables, columns, rows and settings are created only when missing. Nothing existing changes.

Revision ID: 0015_reminders
Revises: 0014_integration_feed
"""
from __future__ import annotations

import time

import sqlalchemy as sa
from alembic import op

revision = '0015_reminders'
down_revision = '0014_integration_feed'
branch_labels = None
depends_on = None

WEEKDAYS = [0, 1, 2, 3, 4]
RULES = (
    # id, name, first_after, every, max_count, notes
    ('standard', 'Standard', 2, 3, 4, 'The rule most envelopes use.'),
    ('gentle', 'Gentle', 5, 7, 2, 'For clients who prefer fewer emails.'),
    ('firm', 'Firm', 1, 2, 6, 'For urgent agreements.'),
    ('off', 'Off', 2, 3, 0, 'No automatic reminders. The invitation and the signed copy are still sent.'),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if 'reminder_rules' not in tables:
        op.create_table('reminder_rules',
                        sa.Column('id', sa.String(length=40), nullable=False),
                        sa.Column('name', sa.String(length=80), nullable=False),
                        sa.Column('description', sa.Text(), nullable=False),
                        sa.Column('first_after', sa.Integer(), nullable=False),
                        sa.Column('every', sa.Integer(), nullable=False),
                        sa.Column('max_count', sa.Integer(), nullable=False),
                        sa.Column('weekdays', sa.JSON(), nullable=False),
                        sa.Column('hour_from', sa.Integer(), nullable=False),
                        sa.Column('hour_to', sa.Integer(), nullable=False),
                        sa.Column('combine', sa.Boolean(), nullable=False),
                        sa.Column('skip_opened_hours', sa.Integer(), nullable=False),
                        sa.Column('remind_viewers', sa.Boolean(), nullable=False),
                        sa.Column('allow_signer_stop', sa.Boolean(), nullable=False),
                        sa.Column('notify_sender_after_last', sa.Boolean(), nullable=False),
                        sa.Column('active', sa.Boolean(), nullable=False),
                        sa.Column('builtin', sa.Boolean(), nullable=False),
                        sa.Column('created', sa.Float(), nullable=False),
                        sa.Column('updated', sa.Float(), nullable=False),
                        sa.Column('updated_by', sa.String(length=32), nullable=True),
                        sa.PrimaryKeyConstraint('id'))
    if 'reminder_optouts' not in tables:
        op.create_table('reminder_optouts',
                        sa.Column('id', sa.String(length=32), nullable=False),
                        sa.Column('email', sa.String(length=200), nullable=False),
                        sa.Column('name', sa.String(length=150), nullable=False),
                        sa.Column('entity', sa.String(length=100), nullable=True),
                        sa.Column('reason', sa.Text(), nullable=False),
                        sa.Column('until', sa.Float(), nullable=True),
                        sa.Column('created', sa.Float(), nullable=False),
                        sa.Column('created_by', sa.String(length=32), nullable=True),
                        sa.Column('created_by_name', sa.String(length=150), nullable=False),
                        sa.Column('removed_at', sa.Float(), nullable=True),
                        sa.Column('removed_by', sa.String(length=32), nullable=True),
                        sa.PrimaryKeyConstraint('id'))
    bind.exec_driver_sql('CREATE INDEX IF NOT EXISTS ix_reminder_optouts_email ON reminder_optouts (email)')
    have = {c['name'] for c in inspector.get_columns('esign_recipients')}
    for column in (sa.Column('reminders_off', sa.Boolean(), nullable=False, server_default=sa.false()),
                   sa.Column('reminders_off_source', sa.String(length=12), nullable=True),
                   sa.Column('reminders_paused_until', sa.Float(), nullable=True),
                   sa.Column('reminder_anchor', sa.Float(), nullable=True),
                   sa.Column('reminder_final_notice_at', sa.Float(), nullable=True)):
        if column.name not in have:
            op.add_column('esign_recipients', column)
    if 'handed_at' not in {c['name'] for c in inspector.get_columns('mail_outbox')}:
        op.add_column('mail_outbox', sa.Column('handed_at', sa.Float(), nullable=True))
    now = time.time()
    rules = sa.table('reminder_rules', *(sa.column(n, sa.JSON) if n == 'weekdays' else sa.column(n) for n in (
        'id', 'name', 'description', 'first_after', 'every', 'max_count', 'weekdays', 'hour_from', 'hour_to', 'combine', 'skip_opened_hours', 'remind_viewers',
        'allow_signer_stop', 'notify_sender_after_last', 'active', 'builtin', 'created', 'updated', 'updated_by')))
    present = set(bind.execute(sa.select(rules.c.id)).scalars())
    for rid, name, first, every, most, notes in RULES:
        if rid not in present:
            bind.execute(rules.insert().values(id=rid, name=name, description=notes, first_after=first, every=every, max_count=most, weekdays=WEEKDAYS,
                                               hour_from=9, hour_to=17, combine=True, skip_opened_hours=24, remind_viewers=False, allow_signer_stop=True,
                                               notify_sender_after_last=most > 0, active=True, builtin=True, created=now, updated=now, updated_by=None))
    settings = sa.table('settings', sa.column('key', sa.String), sa.column('value', sa.JSON))
    if not bind.execute(sa.select(settings.c.key).where(settings.c.key == 'reminders:installed_at')).first():
        bind.execute(settings.insert().values(key='reminders:installed_at', value=now))


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql('DROP INDEX IF EXISTS ix_reminder_optouts_email')
    op.drop_table('reminder_optouts')
    op.drop_table('reminder_rules')
    with op.batch_alter_table('esign_recipients') as batch:
        for name in ('reminder_final_notice_at', 'reminder_anchor', 'reminders_paused_until', 'reminders_off_source', 'reminders_off'):
            batch.drop_column(name)
    with op.batch_alter_table('mail_outbox') as batch:
        batch.drop_column('handed_at')
    settings = sa.table('settings', sa.column('key', sa.String))
    bind.execute(settings.delete().where(settings.c.key.like('reminders:%')))

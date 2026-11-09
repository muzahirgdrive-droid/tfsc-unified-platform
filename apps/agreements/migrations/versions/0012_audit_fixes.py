"""Audit fixes (November 2026): automatic sign-out, mailbox pauses, faster lists, a protected signing history

Everything here is added; nothing existing is changed, moved or removed, so every client, agreement, envelope, signature and
certificate stays exactly as it is, and the previous release keeps working on this database after a step-back.

* sessions.created, esign_sessions.created: when a session began. The idle limit slides with activity but never past the
  absolute limit. Sessions from before this release have no value and simply run out as they would have.
* mailboxes.paused_until / pause_kind / pause_reason / pause_count: sending for one company mailbox waits (sign-in refused,
  sending limit reached, server unreachable) while its queue stays intact. sent_day / sent_count: emails sent today.
  support_email: the address clients of that company are told to write to.
* users.custom_services: whether a person limited to certain services may still add a custom service.
* esign_events.seal: a keyed seal of each history entry (the hash chain alone can be recomputed by anyone who can write to
  the database; the seal cannot).
* Indexes for the queries that read whole tables: fields by recipient, envelopes by agreement and by client, recipients by
  lower-case email, history by time, completed-work queue, generated documents by agreement.
* The signing history (esign_events) can no longer be changed, and entries can only be removed together with a draft or
  template that is deleted (triggers, like the client-number registry).

Additive and idempotent: every step checks what is already there, so running it again after a step-back is safe.

Revision ID: 0012_audit_fixes
Revises: 0011_client_trash
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0012_audit_fixes'
down_revision = '0011_client_trash'
branch_labels = None
depends_on = None

COLUMNS = {
    'sessions': [('created', sa.Float())],
    'esign_sessions': [('created', sa.Float())],
    'mailboxes': [('paused_until', sa.Float()), ('pause_kind', sa.String(length=12)), ('pause_reason', sa.Text()), ('pause_count', sa.Integer()),
                  ('sent_day', sa.String(length=10)), ('sent_count', sa.Integer()), ('support_email', sa.String(length=200))],
    'users': [('custom_services', sa.Boolean())],
    'esign_events': [('seal', sa.String(length=64))],
}

# name: (table, expression list, optional WHERE). Written as SQL so the same text works on PostgreSQL and SQLite.
INDEXES = {
    'ix_esign_fields_recipient': ('esign_fields', 'recipient_id', None),
    'ix_esign_env_contract': ('esign_envelopes', 'contract_id', None),
    'ix_esign_env_client_status': ('esign_envelopes', 'client_id, status', None),
    'ix_esign_env_entity_status': ('esign_envelopes', 'entity, status, updated', None),
    'ix_esign_env_open': ('esign_envelopes', 'status', "status IN ('sent', 'completing')"),
    'ix_esign_rcpt_lower_email': ('esign_recipients', 'lower(email), status', None),
    'ix_esign_events_at': ('esign_events', 'at', None),
    'ix_esign_events_env_at': ('esign_events', 'envelope_id, at', None),
    'ix_esign_docs_source': ('esign_documents', 'source_document_id', None),
    'ix_contracts_entity_created': ('contracts', 'entity, created', None),
    'ix_clients_live_updated': ('clients', 'updated', 'deleted_at IS NULL'),
    'ix_mail_outbox_entity_queue': ('mail_outbox', 'entity, state, next_attempt', None),
}

KEEP = 'The signing history is permanent: an entry can never be changed.'
KEEP_DELETE = 'The signing history is permanent: entries are removed only together with a draft or template that is deleted.'

SQLITE_TRIGGERS = {
    'trg_esign_events_no_update': f"""CREATE TRIGGER trg_esign_events_no_update BEFORE UPDATE ON esign_events
        WHEN NEW.id IS NOT OLD.id OR NEW.envelope_id IS NOT OLD.envelope_id OR NEW.seq IS NOT OLD.seq OR NEW.at IS NOT OLD.at OR NEW.kind IS NOT OLD.kind
          OR NEW.actor IS NOT OLD.actor OR NEW.recipient_id IS NOT OLD.recipient_id OR NEW.ip IS NOT OLD.ip OR NEW.user_agent IS NOT OLD.user_agent
          OR NEW.detail IS NOT OLD.detail OR NEW.prev_hash IS NOT OLD.prev_hash OR NEW.hash IS NOT OLD.hash OR (OLD.seal IS NOT NULL AND NEW.seal IS NOT OLD.seal)
        BEGIN SELECT RAISE(ABORT, '{KEEP}'); END""",
    'trg_esign_events_no_delete': f"""CREATE TRIGGER trg_esign_events_no_delete BEFORE DELETE ON esign_events
        WHEN EXISTS (SELECT 1 FROM esign_envelopes e WHERE e.id = OLD.envelope_id AND e.status NOT IN ('draft', 'template'))
        BEGIN SELECT RAISE(ABORT, '{KEEP_DELETE}'); END""",
}

POSTGRES = [
    f"""CREATE OR REPLACE FUNCTION tfsc_esign_events_keep() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'UPDATE' THEN
        -- the only change ever allowed: adding the seal to an entry that has none yet
        IF OLD.seal IS NULL AND NEW.seal IS NOT NULL AND (to_jsonb(NEW) - 'seal') = (to_jsonb(OLD) - 'seal') THEN
          RETURN NEW;
        END IF;
        RAISE EXCEPTION '{KEEP}';
      END IF;
      IF EXISTS (SELECT 1 FROM esign_envelopes e WHERE e.id = OLD.envelope_id AND e.status NOT IN ('draft', 'template')) THEN
        RAISE EXCEPTION '{KEEP_DELETE}';
      END IF;
      RETURN OLD;
    END $$ LANGUAGE plpgsql""",
    'DROP TRIGGER IF EXISTS trg_esign_events_keep ON esign_events',
    'CREATE TRIGGER trg_esign_events_keep BEFORE UPDATE OR DELETE ON esign_events FOR EACH ROW EXECUTE FUNCTION tfsc_esign_events_keep()',
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table, columns in COLUMNS.items():
        if table not in tables:
            continue
        have = {c['name'] for c in inspector.get_columns(table)}
        for name, kind in columns:
            if name not in have:
                op.add_column(table, sa.Column(name, kind, nullable=True))
    for name, (table, expression, where) in INDEXES.items():
        if table in tables:
            bind.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS {name} ON {table} ({expression})' + (f' WHERE {where}' if where else ''))
    if 'esign_events' in tables:
        if bind.dialect.name == 'postgresql':
            for statement in POSTGRES:
                bind.exec_driver_sql(statement)
            bind.exec_driver_sql('ANALYZE')
        else:
            present = set(bind.execute(sa.text("SELECT name FROM sqlite_master WHERE type = 'trigger'")).scalars())
            for name, statement in SQLITE_TRIGGERS.items():
                if name not in present:
                    bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.exec_driver_sql('DROP TRIGGER IF EXISTS trg_esign_events_keep ON esign_events')
        bind.exec_driver_sql('DROP FUNCTION IF EXISTS tfsc_esign_events_keep()')
    else:
        for name in SQLITE_TRIGGERS:
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS {name}')
    for name in INDEXES:
        bind.exec_driver_sql(f'DROP INDEX IF EXISTS {name}')
    for table, columns in COLUMNS.items():
        with op.batch_alter_table(table, schema=None) as batch_op:
            for name, _ in columns:
                batch_op.drop_column(name)

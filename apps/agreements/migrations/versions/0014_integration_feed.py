"""The read-only feed for TFS Onboarding and Client Success (OCS): TFS Agreements Integration API v1

* integration_feed: the numbered list of changes the feed serves. Each row is the complete picture of one client
  (client.snapshot) or the notice that a client is no longer published (client.withdrawn).
* integration_clients: for each client, the hash of the picture last published, so an unchanged client is never
  published again.
* integration_tokens: the read-only tokens OCS uses (stored as SHA-256 hashes; the token itself is shown once).
* settings: integration_instance_id (a random id of this installation, written once and never changed; a restore keeps
  it) and integration_watermark (how far the publisher has read).
* Indexes on the `updated` columns of esign_envelopes, clients, client_agreements and client_contacts, which the publisher
  reads to find what changed. On PostgreSQL they are NOT built here: a migration runs inside the start-up transaction,
  and building an index there would block writes to those tables while it runs. The publisher builds them with
  CREATE INDEX CONCURRENTLY, outside any transaction, the first time it runs (integration_v1.ensure_indexes). The local
  edition (SQLite) builds them here.

Additive and idempotent: tables, indexes and settings are created only when missing. Nothing existing changes.

Revision ID: 0014_integration_feed
Revises: 0013_followups_margins
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = '0014_integration_feed'
down_revision = '0013_followups_margins'
branch_labels = None
depends_on = None

SOURCE_INDEXES = (('ix_esign_env_updated', 'esign_envelopes'), ('ix_clients_updated', 'clients'),
                  ('ix_client_agreements_updated', 'client_agreements'), ('ix_client_contacts_updated', 'client_contacts'))


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if 'integration_feed' not in tables:
        op.create_table('integration_feed',
                        sa.Column('seq', sa.BigInteger(), autoincrement=False, nullable=False),
                        sa.Column('client_id', sa.String(length=32), nullable=False),
                        sa.Column('kind', sa.String(length=20), nullable=False),
                        sa.Column('snapshot', sa.JSON(), nullable=True),
                        sa.Column('snapshot_hash', sa.String(length=64), nullable=True),
                        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
                        sa.PrimaryKeyConstraint('seq'))
    if 'integration_clients' not in tables:
        op.create_table('integration_clients',
                        sa.Column('client_id', sa.String(length=32), nullable=False),
                        sa.Column('snapshot_hash', sa.String(length=64), nullable=False),
                        sa.Column('last_seq', sa.BigInteger(), nullable=False),
                        sa.Column('published', sa.Boolean(), nullable=False),
                        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
                        sa.PrimaryKeyConstraint('client_id'))
    if 'integration_tokens' not in tables:
        op.create_table('integration_tokens',
                        sa.Column('id', sa.String(length=32), nullable=False),
                        sa.Column('name', sa.String(length=100), nullable=False),
                        sa.Column('token_hash', sa.String(length=64), nullable=False),
                        sa.Column('created', sa.DateTime(timezone=True), nullable=False),
                        sa.Column('created_by', sa.String(length=32), nullable=True),
                        sa.Column('last_used', sa.DateTime(timezone=True), nullable=True),
                        sa.Column('last_ip', sa.String(length=64), nullable=True),
                        sa.Column('revoked', sa.DateTime(timezone=True), nullable=True),
                        sa.PrimaryKeyConstraint('id'),
                        sa.UniqueConstraint('token_hash', name='uq_integration_token_hash'))
    bind.exec_driver_sql('CREATE INDEX IF NOT EXISTS ix_integration_feed_client ON integration_feed (client_id, seq)')
    if bind.dialect.name != 'postgresql':
        for name, table in SOURCE_INDEXES:
            bind.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS {name} ON {table} (updated)')
    settings = sa.table('settings', sa.column('key', sa.String), sa.column('value', sa.JSON))
    present = set(bind.execute(sa.select(settings.c.key).where(settings.c.key.in_(('integration_instance_id', 'integration_watermark')))).scalars())
    if 'integration_instance_id' not in present:
        bind.execute(settings.insert().values(key='integration_instance_id', value=str(uuid.uuid4())))
    if 'integration_watermark' not in present:
        bind.execute(settings.insert().values(key='integration_watermark', value=0))


def downgrade() -> None:
    bind = op.get_bind()
    for name, _ in SOURCE_INDEXES:
        bind.exec_driver_sql(f'DROP INDEX IF EXISTS {name}')
    bind.exec_driver_sql('DROP INDEX IF EXISTS ix_integration_feed_client')
    op.drop_table('integration_tokens')
    op.drop_table('integration_clients')
    op.drop_table('integration_feed')
    settings = sa.table('settings', sa.column('key', sa.String))
    bind.execute(settings.delete().where(settings.c.key.like('integration_%')))

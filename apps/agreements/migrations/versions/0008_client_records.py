"""Client records: a permanent client number for every client, past signed agreements, and the import staging area

Adds `clients.client_number` (CL-0001 onward, unique) and numbers every existing client in the order it was created (the
last update when the creation time is unknown, then the id). Adds `client_agreements` (agreements signed before this portal:
by email, DocuSign or Jotform) and the staging tables of "Import previous clients" (`client_imports`, `client_import_files`).

Additive only: ADD COLUMN, CREATE TABLE and CREATE INDEX, so SQLite never rebuilds a table and every client, agreement and
envelope out for signature is kept as it is. Every step checks what is already there: if an update was stepped back after
this ran (agreements.sh rollback) and clients were created meanwhile without a number, running it again numbers only those,
after the numbers already given. Numbers are never reused: the last number given is kept in settings ("client_numbers").

Revision ID: 0008_client_records
Revises: 0007_test_marks
"""
from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision = '0008_client_records'
down_revision = '0007_test_marks'
branch_labels = None
depends_on = None

COUNTER = 'client_numbers'


def number(n: int) -> str:
    return f'CL-{n:04d}'


def parse(value) -> int:
    m = re.fullmatch(r'CL-(\d+)', value or '')
    return int(m.group(1)) if m else 0


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if 'client_number' not in {c['name'] for c in inspector.get_columns('clients')}:
        op.add_column('clients', sa.Column('client_number', sa.String(length=20), nullable=True))
    if 'ix_clients_client_number' not in {i['name'] for i in inspector.get_indexes('clients')}:
        op.create_index('ix_clients_client_number', 'clients', ['client_number'], unique=True)

    if 'client_agreements' not in tables:
        op.create_table(
            'client_agreements',
            sa.Column('id', sa.String(length=32), nullable=False),
            sa.Column('client_id', sa.String(length=32), nullable=False),
            sa.Column('entity', sa.String(length=100), nullable=False),
            sa.Column('kind', sa.String(length=20), nullable=False),
            sa.Column('title', sa.String(length=200), nullable=False),
            sa.Column('source', sa.String(length=20), nullable=False),
            sa.Column('signed_on', sa.String(length=10), nullable=True),
            sa.Column('effective_on', sa.String(length=10), nullable=True),
            sa.Column('client_signer', sa.String(length=200), nullable=False),
            sa.Column('company_signer', sa.String(length=200), nullable=False),
            sa.Column('reference', sa.String(length=120), nullable=False),
            sa.Column('notes', sa.Text(), nullable=False),
            sa.Column('file_id', sa.String(length=32), nullable=True),
            sa.Column('created', sa.Float(), nullable=False),
            sa.Column('created_by', sa.String(length=32), nullable=True),
            sa.Column('updated', sa.Float(), nullable=False),
            sa.Column('test', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
            sa.ForeignKeyConstraint(['file_id'], ['client_files.id']),
            sa.ForeignKeyConstraint(['created_by'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
    indexes = {i['name'] for i in sa.inspect(bind).get_indexes('client_agreements')}
    if 'ix_client_agreements_client' not in indexes:
        op.create_index('ix_client_agreements_client', 'client_agreements', ['client_id', 'signed_on'], unique=False)
    if 'ix_client_agreements_file' not in indexes:
        op.create_index('ix_client_agreements_file', 'client_agreements', ['file_id'], unique=False)

    if 'client_imports' not in tables:
        op.create_table(
            'client_imports',
            sa.Column('id', sa.String(length=32), nullable=False),
            sa.Column('owner_id', sa.String(length=32), nullable=False),
            sa.Column('created', sa.Float(), nullable=False),
            sa.Column('updated', sa.Float(), nullable=False),
            sa.Column('expires', sa.Float(), nullable=False),
            sa.Column('sheet', sa.JSON(), nullable=True),
            sa.Column('choices', sa.JSON(), nullable=False),
            sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
    indexes = {i['name'] for i in sa.inspect(bind).get_indexes('client_imports')}
    if 'ix_client_imports_owner_id' not in indexes:
        op.create_index('ix_client_imports_owner_id', 'client_imports', ['owner_id'], unique=False)
    if 'ix_client_imports_expires' not in indexes:
        op.create_index('ix_client_imports_expires', 'client_imports', ['expires'], unique=False)

    if 'client_import_files' not in tables:
        op.create_table(
            'client_import_files',
            sa.Column('id', sa.String(length=32), nullable=False),
            sa.Column('import_id', sa.String(length=32), nullable=False),
            sa.Column('name', sa.String(length=200), nullable=False),
            sa.Column('size', sa.Integer(), nullable=False),
            sa.Column('sha256', sa.String(length=64), nullable=False),
            sa.Column('scan', sa.String(length=20), nullable=False),
            sa.Column('pages', sa.Integer(), nullable=True),
            sa.Column('found', sa.JSON(), nullable=False),
            sa.Column('body', sa.LargeBinary(), nullable=False),
            sa.Column('created', sa.Float(), nullable=False),
            sa.ForeignKeyConstraint(['import_id'], ['client_imports.id']),
            sa.PrimaryKeyConstraint('id'),
        )
    if 'ix_client_import_files_import_id' not in {i['name'] for i in sa.inspect(bind).get_indexes('client_import_files')}:
        op.create_index('ix_client_import_files_import_id', 'client_import_files', ['import_id'], unique=False)

    backfill(bind)


def backfill(bind) -> None:
    """Number the clients that have no number yet, after the numbers already given (oldest client first)."""
    clients = sa.table('clients', sa.column('id', sa.String), sa.column('created', sa.Float), sa.column('updated', sa.Float),
                       sa.column('client_number', sa.String))
    settings = sa.table('settings', sa.column('key', sa.String), sa.column('value', sa.JSON))
    last = max((parse(n) for n in bind.execute(sa.select(clients.c.client_number).where(clients.c.client_number.isnot(None))).scalars()), default=0)
    stored = bind.execute(sa.select(settings.c.value).where(settings.c.key == COUNTER)).scalar()
    if isinstance(stored, dict) and isinstance(stored.get('last'), int):
        last = max(last, stored['last'])
    pending = bind.execute(sa.select(clients.c.id).where(clients.c.client_number.is_(None))
                           .order_by(sa.func.coalesce(clients.c.created, clients.c.updated), clients.c.updated, clients.c.id)).scalars().all()
    for client_id in pending:
        last += 1
        bind.execute(sa.update(clients).where(clients.c.id == client_id).values(client_number=number(last)))
    if stored is None:
        bind.execute(sa.insert(settings).values(key=COUNTER, value={'last': last}))
    else:
        bind.execute(sa.update(settings).where(settings.c.key == COUNTER).values(value={'last': last}))


def downgrade() -> None:
    op.drop_index('ix_client_import_files_import_id', table_name='client_import_files')
    op.drop_table('client_import_files')
    op.drop_index('ix_client_imports_expires', table_name='client_imports')
    op.drop_index('ix_client_imports_owner_id', table_name='client_imports')
    op.drop_table('client_imports')
    op.drop_index('ix_client_agreements_file', table_name='client_agreements')
    op.drop_index('ix_client_agreements_client', table_name='client_agreements')
    op.drop_table('client_agreements')
    op.drop_index('ix_clients_client_number', table_name='clients')
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.drop_column('client_number')
    settings = sa.table('settings', sa.column('key', sa.String))
    op.get_bind().execute(sa.delete(settings).where(settings.c.key == COUNTER))

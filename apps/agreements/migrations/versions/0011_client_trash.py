"""Trash for clients, and client numbers kept for good by the database itself

* clients.deleted_at / deleted_by / purged_at: a Super Admin can delete a client; it waits in Trash for 30 days and can be
  restored exactly as it was, then it is purged (its details are wiped and it stays hidden; its signed agreements and
  certificates remain tied to it as legal records). Columns are added (nullable), so every existing client stays as it is.
* client_number_registry: every client number ever given (backfilled from the clients and the numbering counter, so a number
  given to a client that no longer exists stays taken as well).
* Triggers (SQLite and PostgreSQL): a client's number can never change once given; a number registered to one client can
  never be given to another; numbers written by any code path are registered; registry rows can never be updated or deleted.

Additive and idempotent: every step checks what is already there, so running it again after a rollback is safe. A previous
release running on this database keeps working: its new clients are registered by the triggers.

Revision ID: 0011_client_trash
Revises: 0010_billing_nest_letterhead
"""
from __future__ import annotations

import re
import time

import sqlalchemy as sa
from alembic import op

revision = '0011_client_trash'
down_revision = '0010_billing_nest_letterhead'
branch_labels = None
depends_on = None

PERMANENT = 'Client numbers are permanent: a number given to a client never changes.'
REUSED = 'This client number was given to another client and is never reused.'
KEPT = 'Client numbers are kept for good: the record of a number given can never be changed or removed.'

SQLITE_TRIGGERS = {
    'trg_client_number_permanent': f"""CREATE TRIGGER trg_client_number_permanent BEFORE UPDATE OF client_number ON clients
        WHEN OLD.client_number IS NOT NULL AND (NEW.client_number IS NULL OR NEW.client_number <> OLD.client_number)
        BEGIN SELECT RAISE(ABORT, '{PERMANENT}'); END""",
    'trg_client_number_unique_insert': f"""CREATE TRIGGER trg_client_number_unique_insert BEFORE INSERT ON clients
        WHEN NEW.client_number IS NOT NULL AND EXISTS (SELECT 1 FROM client_number_registry r WHERE r.number = NEW.client_number AND r.client_id <> NEW.id)
        BEGIN SELECT RAISE(ABORT, '{REUSED}'); END""",
    'trg_client_number_unique_update': f"""CREATE TRIGGER trg_client_number_unique_update BEFORE UPDATE OF client_number ON clients
        WHEN NEW.client_number IS NOT NULL AND EXISTS (SELECT 1 FROM client_number_registry r WHERE r.number = NEW.client_number AND r.client_id <> NEW.id)
        BEGIN SELECT RAISE(ABORT, '{REUSED}'); END""",
    'trg_client_number_register_insert': """CREATE TRIGGER trg_client_number_register_insert AFTER INSERT ON clients WHEN NEW.client_number IS NOT NULL
        BEGIN INSERT OR IGNORE INTO client_number_registry (number, client_id, issued) VALUES (NEW.client_number, NEW.id, CAST(strftime('%s', 'now') AS REAL)); END""",
    'trg_client_number_register_update': """CREATE TRIGGER trg_client_number_register_update AFTER UPDATE OF client_number ON clients WHEN NEW.client_number IS NOT NULL
        BEGIN INSERT OR IGNORE INTO client_number_registry (number, client_id, issued) VALUES (NEW.client_number, NEW.id, CAST(strftime('%s', 'now') AS REAL)); END""",
    'trg_client_number_registry_no_delete': f"""CREATE TRIGGER trg_client_number_registry_no_delete BEFORE DELETE ON client_number_registry
        BEGIN SELECT RAISE(ABORT, '{KEPT}'); END""",
    'trg_client_number_registry_no_update': f"""CREATE TRIGGER trg_client_number_registry_no_update BEFORE UPDATE ON client_number_registry
        BEGIN SELECT RAISE(ABORT, '{KEPT}'); END""",
}

POSTGRES = [
    f"""CREATE OR REPLACE FUNCTION tfsc_client_number_guard() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'UPDATE' AND OLD.client_number IS NOT NULL AND NEW.client_number IS DISTINCT FROM OLD.client_number THEN
        RAISE EXCEPTION '{PERMANENT}';
      END IF;
      IF NEW.client_number IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM client_number_registry r WHERE r.number = NEW.client_number AND r.client_id <> NEW.id) THEN
          RAISE EXCEPTION '{REUSED}';
        END IF;
        INSERT INTO client_number_registry (number, client_id, issued) VALUES (NEW.client_number, NEW.id, extract(epoch from now()))
          ON CONFLICT (number) DO NOTHING;
      END IF;
      RETURN NEW;
    END $$ LANGUAGE plpgsql""",
    f"""CREATE OR REPLACE FUNCTION tfsc_client_number_registry_keep() RETURNS trigger AS $$
    BEGIN RAISE EXCEPTION '{KEPT}'; END $$ LANGUAGE plpgsql""",
    'DROP TRIGGER IF EXISTS trg_client_number_guard ON clients',
    'CREATE TRIGGER trg_client_number_guard BEFORE INSERT OR UPDATE OF client_number ON clients FOR EACH ROW EXECUTE FUNCTION tfsc_client_number_guard()',
    'DROP TRIGGER IF EXISTS trg_client_number_registry_keep ON client_number_registry',
    'CREATE TRIGGER trg_client_number_registry_keep BEFORE UPDATE OR DELETE ON client_number_registry FOR EACH ROW EXECUTE FUNCTION tfsc_client_number_registry_keep()',
]


def _number(n: int) -> str:
    return f'CL-{n:04d}'


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    have = {c['name'] for c in inspector.get_columns('clients')}
    for name in ('deleted_at', 'purged_at'):
        if name not in have:
            op.add_column('clients', sa.Column(name, sa.Float(), nullable=True))
    if 'deleted_by' not in have:
        op.add_column('clients', sa.Column('deleted_by', sa.String(length=32), nullable=True))
    if 'client_number_registry' not in inspector.get_table_names():
        op.create_table('client_number_registry',
                        sa.Column('number', sa.String(length=20), nullable=False),
                        sa.Column('client_id', sa.String(length=32), nullable=False),
                        sa.Column('issued', sa.Float(), nullable=False),
                        sa.PrimaryKeyConstraint('number'))

    # Backfill: every number in use, then every number below the counter that no client holds any more (still taken).
    now = time.time()
    registry = sa.table('client_number_registry', sa.column('number', sa.String), sa.column('client_id', sa.String), sa.column('issued', sa.Float))
    clients = sa.table('clients', sa.column('id', sa.String), sa.column('client_number', sa.String), sa.column('created', sa.Float), sa.column('updated', sa.Float))
    settings = sa.table('settings', sa.column('key', sa.String), sa.column('value', sa.JSON))
    known = set(bind.execute(sa.select(registry.c.number)).scalars())
    top = 0
    for cid, number, created, updated in bind.execute(sa.select(clients.c.id, clients.c.client_number, clients.c.created, clients.c.updated)
                                                      .where(clients.c.client_number.isnot(None))).all():
        m = re.fullmatch(r'CL-(\d+)', number or '')
        top = max(top, int(m.group(1)) if m else 0)
        if number not in known:
            bind.execute(registry.insert().values(number=number, client_id=cid, issued=created or updated or now))
            known.add(number)
    counter = bind.execute(sa.select(settings.c.value).where(settings.c.key == 'client_numbers')).scalar()
    last = counter.get('last', 0) if isinstance(counter, dict) and isinstance(counter.get('last'), int) else 0
    for n in range(1, max(top, last) + 1):
        if _number(n) not in known:
            bind.execute(registry.insert().values(number=_number(n), client_id='', issued=now))   # given before, holder gone: stays taken
            known.add(_number(n))

    if bind.dialect.name == 'postgresql':
        for statement in POSTGRES:
            bind.exec_driver_sql(statement)
    else:
        present = set(bind.execute(sa.text("SELECT name FROM sqlite_master WHERE type = 'trigger'")).scalars())
        for name, statement in SQLITE_TRIGGERS.items():
            if name not in present:
                bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        bind.exec_driver_sql('DROP TRIGGER IF EXISTS trg_client_number_guard ON clients')
        bind.exec_driver_sql('DROP TRIGGER IF EXISTS trg_client_number_registry_keep ON client_number_registry')
        bind.exec_driver_sql('DROP FUNCTION IF EXISTS tfsc_client_number_guard()')
        bind.exec_driver_sql('DROP FUNCTION IF EXISTS tfsc_client_number_registry_keep()')
    else:
        for name in SQLITE_TRIGGERS:
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS {name}')
    op.drop_table('client_number_registry')
    with op.batch_alter_table('clients', schema=None) as batch_op:
        for name in ('purged_at', 'deleted_by', 'deleted_at'):
            batch_op.drop_column(name)

"""Alembic environment for the CRM database.

Migrations are explicit and frozen: they never import live application metadata,
so a later model change cannot silently rewrite history. `target_metadata` is only
used by `alembic check` / autogenerate when authoring a new revision.
"""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
URL = os.getenv('CRM_DATABASE_URL') or config.get_main_option('sqlalchemy.url')
if not URL:
    raise SystemExit('Set CRM_DATABASE_URL (postgresql+psycopg://...) before running migrations.')

def _target_metadata():
    if os.getenv('CRM_ALEMBIC_AUTOGEN') != '1':
        return None
    from tfsc_crm.db.models import metadata
    return metadata

def run_offline() -> None:
    context.configure(url=URL, target_metadata=_target_metadata(), literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()

def run_online() -> None:
    engine = create_engine(URL, poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=_target_metadata(), compare_type=True,
                          transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()

run_offline() if context.is_offline_mode() else run_online()

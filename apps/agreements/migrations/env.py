"""Alembic environment for the Agreements database (SQLite locally, PostgreSQL in hosted mode).

Migrations are frozen files; live metadata is loaded only when authoring a revision (TFSC_ALEMBIC_AUTOGEN=1).
`database.init_db()` runs migrations at start-up, so a fresh install and an upgrade take the same path.
"""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config


def _target_metadata():
    if os.getenv('TFSC_ALEMBIC_AUTOGEN') != '1':
        return None
    import portal  # noqa: F401  (registers every table, including the bridge and OIDC ones)
    import database
    return database.meta


def run_offline() -> None:
    context.configure(url=os.getenv('DATABASE_URL'), target_metadata=_target_metadata(), literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    conn = config.attributes.get('connection')
    if conn is not None:  # called from database.init_db() inside its transaction
        context.configure(connection=conn, target_metadata=_target_metadata(), render_as_batch=True, compare_type=True)
        context.run_migrations()
        return
    import database
    engine = create_engine(os.getenv('DATABASE_URL') or str(database.engine.url), poolclass=pool.NullPool)
    with engine.connect() as c:
        context.configure(connection=c, target_metadata=_target_metadata(), render_as_batch=True, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_offline() if context.is_offline_mode() else run_online()

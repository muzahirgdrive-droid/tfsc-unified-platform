"""Run the Agreements schema migrations (Alembic) inside a caller's transaction.

A database created before Alembic (tables made by create_all, no alembic_version) first gets any missing tables
the same way as before, is then stamped at the baseline, and only then upgraded, so existing installs keep every
row and follow the same path as new ones from here on.
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

APP = Path(__file__).resolve().parent
BASELINE = '0001_agreements_baseline'


def config(connection=None) -> Config:
    cfg = Config(str(APP / 'alembic.ini'))
    cfg.set_main_option('script_location', str(APP / 'migrations'))
    if connection is not None:
        cfg.attributes['connection'] = connection
    return cfg


def upgrade_to_head(connection, metadata) -> str:
    """Returns 'fresh', 'stamped' or 'current' (what kind of database it was)."""
    tables = set(inspect(connection).get_table_names())
    kind = 'current' if 'alembic_version' in tables else ('stamped' if 'users' in tables else 'fresh')
    cfg = config(connection)
    if kind == 'stamped':
        metadata.create_all(connection)  # pre-Alembic install: same behaviour as before for any missing table
        command.stamp(cfg, BASELINE)
    command.upgrade(cfg, 'head')
    return kind

"""Run the Agreements schema migrations (Alembic) inside a caller's transaction.

A database created before Alembic (tables made by create_all, no alembic_version) first gets any missing tables
the same way as before, is then stamped at the baseline, and only then upgraded, so existing installs keep every
row and follow the same path as new ones from here on.
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, inspect

APP = Path(__file__).resolve().parent
BASELINE = '0001_agreements_baseline'
BASELINE_TABLES = {'audit', 'clients', 'contracts', 'crm_account_links', 'crm_actor_links', 'crm_bridge_handoffs', 'crm_bridge_nonces', 'crm_client_links',
                   'crm_provision_requests', 'agreement_reviews', 'documents', 'jobs', 'oidc_accounts', 'oidc_states', 'sessions', 'settings', 'tfsc_outbox',
                   'throttle', 'users'}
# Columns that later migrations add to baseline tables: a pre-Alembic install gets them from those migrations.
LATER_COLUMNS = {'clients': ('profile', 'created'), 'audit': ('ip', 'user_agent')}
LATER_INDEXES = {'audit': ('ix_audit_created', 'ix_audit_actor_created')}


def config(connection=None) -> Config:
    cfg = Config(str(APP / 'alembic.ini'))
    cfg.set_main_option('script_location', str(APP / 'migrations'))
    if connection is not None:
        cfg.attributes['connection'] = connection
    return cfg


def baseline_metadata(metadata) -> MetaData:
    """The baseline tables as they were at the baseline revision."""
    baseline = MetaData()
    for table in metadata.sorted_tables:
        if table.name in BASELINE_TABLES:
            copy = table.to_metadata(baseline)
            for name in LATER_COLUMNS.get(table.name, ()):
                copy._columns.remove(copy.c[name])
            for index in [i for i in copy.indexes if i.name in LATER_INDEXES.get(table.name, ())]:
                copy.indexes.discard(index)
    return baseline


def upgrade_to_head(connection, metadata) -> str:
    """Returns 'fresh', 'stamped' or 'current' (what kind of database it was)."""
    tables = set(inspect(connection).get_table_names())
    kind = 'current' if 'alembic_version' in tables else ('stamped' if 'users' in tables else 'fresh')
    cfg = config(connection)
    if kind == 'stamped':
        # pre-Alembic install: create any missing table of the baseline the same way as before; later tables come from
        # their own migrations
        baseline_metadata(metadata).create_all(connection)
        command.stamp(cfg, BASELINE)
    command.upgrade(cfg, 'head')
    return kind

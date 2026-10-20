"""Engine and transaction scopes.

Every request transaction runs as the restricted application role with
`app.org_id` / `app.actor_id` set (RLS, ADR-0007). `system_tx()` runs as the
connecting (owner) role and is reserved for authentication bootstrap, migrations
helpers and background jobs that set the org explicitly.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, text

from ..config import get_settings

_engine: Engine | None = None
_ROLE = re.compile(r'^[a-z_][a-z0-9_]{0,62}$')


def engine() -> Engine:
    global _engine
    if _engine is None:
        s = get_settings()
        _engine = create_engine(s.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20, pool_recycle=1800, hide_parameters=True)
    return _engine


def dispose() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def _apply_scope(c: Connection, org_id: uuid.UUID | str | None, actor_id: uuid.UUID | str | None) -> None:
    role = get_settings().db_app_role
    if role:
        if not _ROLE.match(role):
            raise RuntimeError('Invalid CRM_DB_APP_ROLE')
        c.execute(text(f'SET LOCAL ROLE {role}'))
    c.execute(text("SELECT set_config('app.org_id', :o, true), set_config('app.actor_id', :a, true)"),
              {'o': str(org_id) if org_id else '', 'a': str(actor_id) if actor_id else ''})


@contextmanager
def tx(org_id: uuid.UUID | str, actor_id: uuid.UUID | str | None = None) -> Iterator[Connection]:
    """Read-write transaction scoped to one org under RLS."""
    with engine().begin() as c:
        _apply_scope(c, org_id, actor_id)
        yield c


@contextmanager
def read(org_id: uuid.UUID | str, actor_id: uuid.UUID | str | None = None) -> Iterator[Connection]:
    with engine().begin() as c:
        c.execute(text('SET TRANSACTION READ ONLY'))
        _apply_scope(c, org_id, actor_id)
        yield c


@contextmanager
def system_tx() -> Iterator[Connection]:
    with engine().begin() as c:
        yield c

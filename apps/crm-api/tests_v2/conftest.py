"""v2 test harness: a fresh PostgreSQL database per test session, migrated to head.

Uses CRM_TEST_DATABASE_URL (admin URL to an existing database, default: local dev
services from tools/dev_services.sh). Tests connect through the app role so
Row-Level Security is exercised exactly as in production.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

APP = Path(__file__).resolve().parents[1]
ADMIN_URL = os.getenv('CRM_TEST_DATABASE_URL', 'postgresql+psycopg://postgres@127.0.0.1:54329/crm_test')


def create_database(prefix: str) -> str:
    admin = create_engine(ADMIN_URL, isolation_level='AUTOCOMMIT')
    name = f'{prefix}_{uuid.uuid4().hex[:10]}'
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE {name}'))
    admin.dispose()
    return make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)


def drop_database(url: str) -> None:
    name = make_url(url).database
    admin = create_engine(ADMIN_URL, isolation_level='AUTOCOMMIT')
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)'))
    admin.dispose()


def alembic(url: str, *args: str) -> str:
    env = {**os.environ, 'CRM_DATABASE_URL': url}
    r = subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=APP, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    return r.stdout + r.stderr


@pytest.fixture(scope='session')
def db_url():
    url = create_database('crm_v2_test')
    alembic(url, 'upgrade', 'head')
    os.environ['CRM_DATABASE_URL'] = url
    yield url
    from tfsc_crm.db import session as dbs
    dbs.dispose()
    drop_database(url)

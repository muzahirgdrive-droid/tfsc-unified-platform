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


# ---------------------------------------------------------------------------- API workspace
PW = 'Synthetic-test-password-512!'


class Client:
    """Thin wrapper over TestClient that keeps the CSRF header for one signed-in user."""

    def __init__(self, app, user: dict | None = None):
        from fastapi.testclient import TestClient
        self.http = TestClient(app, base_url='http://testserver')
        self.user = user or {}

    @property
    def id(self) -> str:
        return self.user['id']

    def login(self, email: str):
        r = self.http.post('/api/auth/login', json={'email': email, 'password': PW})
        assert r.status_code == 200, r.text
        self.http.headers['X-CSRF-Token'] = r.json()['csrf']
        self.user = r.json()['user']
        return self

    def __getattr__(self, name):
        return getattr(self.http, name)


@pytest.fixture(scope='session')
def app(db_url, tmp_path_factory):
    os.environ.update({'CRM_DATA_DIR': str(tmp_path_factory.mktemp('crmdata')), 'CRM_MODE': 'test', 'CRM_ORIGIN': 'http://testserver',
                       'CRM_DB_APP_ROLE': 'crm_app'})
    from tfsc_crm.config import get_settings
    get_settings.cache_clear()
    from tfsc_crm.main import create_app
    application = create_app()
    from tfsc_crm.domain import bootstrap
    bootstrap.ensure_setup_key()
    return application


@pytest.fixture(scope='session')
def ws(app):
    """Hierarchy: super > director > manager > {sup, sup2}; sup > {a, b}; sup2 > {c}; group(a leader, b)."""
    from tfsc_crm.domain import bootstrap
    root = Client(app)
    r = root.post('/api/auth/setup', json={'setup_key': bootstrap.read_setup_key(), 'name': 'Root Super', 'email': 'super@crm.test', 'password': PW})
    assert r.status_code == 200, r.text
    root.http.headers['X-CSRF-Token'] = r.json()['csrf']
    root.user = r.json()['user']
    out = {'super': root}

    def mk(key, role, manager, creator='super', **extra):
        body = {'first_name': key.capitalize(), 'last_name': 'Test', 'email': f'{key}@crm.test', 'crm_role': role,
                'manager_id': out[manager].id, 'temporary_password': PW, **extra}
        r = out[creator].post('/api/users', json=body)
        assert r.status_code == 200, r.text
        c = Client(app).login(f'{key}@crm.test')
        r = c.post('/api/auth/password', json={'current_password': PW, 'new_password': PW + 'x'})
        assert r.status_code == 200, r.text
        # restore the shared password for simplicity
        r = c.post('/api/auth/password', json={'current_password': PW + 'x', 'new_password': PW})
        out[key] = Client(app).login(f'{key}@crm.test')

    mk('director', 'director_admin', 'super')
    mk('manager', 'manager', 'director')
    mk('sup', 'supervisor', 'manager', creator='director')
    mk('sup2', 'supervisor', 'manager', creator='director')
    mk('a', 'sales_agent', 'sup', creator='manager', project_id='P0007')
    mk('b', 'sales_agent', 'sup', creator='manager')
    mk('c', 'sales_agent', 'sup2', creator='manager')
    r = out['sup'].post('/api/teams', json={'name': 'Group One', 'kind': 'group', 'supervisor_id': out['sup'].id,
                                             'member_ids': [out['a'].id, out['b'].id], 'leader_id': out['a'].id})
    assert r.status_code == 200, r.text
    out['a'] = Client(app).login('a@crm.test')  # re-login so the session reflects group leadership
    out['app'] = app
    return out


_phone_counter = [1000]


def new_phone() -> str:
    _phone_counter[0] += 1
    return f'(415) 555-{_phone_counter[0] % 10000:04d}'

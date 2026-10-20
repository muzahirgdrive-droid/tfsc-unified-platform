"""Starts the stack the browser E2E tests run against (apps/crm-web/e2e). SYNTHETIC users only.

1. Recreates the database `crm_e2e` (admin URL: E2E_ADMIN_DATABASE_URL, else CRM_TEST_DATABASE_URL, else local dev
   services) and migrates it to head.
2. Starts TFSC Agreements (portal + document worker, SQLite, LibreOffice renderer) when `soffice` is installed and
   E2E_AGREEMENTS is not 0.
3. Seeds users through the public APIs: Super Admin, Director, Manager, 2 Supervisors, 4 agents; the first supervisor
   gets Agreements access (TFSC user, Scribe Align) and a known TFSC password.
4. Runs the CRM API (one process, in-memory SSE broker) serving the built web app, until stopped.

Playwright starts this as its webServer (see playwright.config.ts). Ports: E2E_CRM_PORT (8799), E2E_TFSC_PORT (8798).
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[1]
PW = 'E2E-password-2026!'  # synthetic accounts in a throwaway database
TFSC_ROOT = ('root@tfsc.e2e', PW)
SECRET = 'e2e-bridge-secret-' + 'x' * 40
CRM_PORT = int(os.getenv('E2E_CRM_PORT', '8799'))
TFSC_PORT = int(os.getenv('E2E_TFSC_PORT', '8798'))
CRM = f'http://127.0.0.1:{CRM_PORT}'
TFSC = f'http://127.0.0.1:{TFSC_PORT}'
ADMIN_URL = os.getenv('E2E_ADMIN_DATABASE_URL') or os.getenv('CRM_TEST_DATABASE_URL') or 'postgresql+psycopg://postgres@127.0.0.1:54329/crm_test'
USERS = [  # key, first, last, role, manager key, project
    ('director', 'Daniel', 'Director', 'director_admin', 'super', None),
    ('manager', 'Mona', 'Manager', 'manager', 'director', None),
    ('sup', 'Samir', 'Supervisor', 'supervisor', 'manager', 'P0007'),
    ('sup2', 'Selena', 'Supervisor', 'supervisor', 'manager', None),
    ('agent1', 'Ava', 'Rahman', 'sales_agent', 'sup', 'P0007'),
    ('agent2', 'Bilal', 'Lopez', 'sales_agent', 'sup', 'P0007'),
    ('agent3', 'Carmen', 'Siddiqui', 'sales_agent', 'sup', 'P0007'),
    ('agent4', 'Daniyal', 'Chen', 'sales_agent', 'sup2', None),
]
procs: list[subprocess.Popen] = []


def log(msg: str) -> None:
    print(f'[e2e-stack] {msg}', flush=True)


def recreate_database() -> str:
    url = make_url(ADMIN_URL).set(database='crm_e2e').render_as_string(hide_password=False)
    admin = create_engine(ADMIN_URL, isolation_level='AUTOCOMMIT')
    with admin.connect() as c:
        c.execute(text('DROP DATABASE IF EXISTS crm_e2e WITH (FORCE)'))
        c.execute(text('CREATE DATABASE crm_e2e'))
    admin.dispose()
    r = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=APP, env={**os.environ, 'CRM_DATABASE_URL': url},
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(r.stderr[-3000:])
    return url


def wait(url: str, timeout: float = 60, host: str | None = None) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, timeout=1, headers={'Host': host} if host else None).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise SystemExit(f'{url} did not start')


def start_tfsc(work: Path) -> dict[str, str]:
    env = {**os.environ, 'TFSC_MODE': 'local', 'TFSC_ORIGIN': TFSC, 'TFSC_DATA_DIR': str(work / 'tfsc-data'),
           'DATABASE_URL': f'sqlite:///{work}/tfsc.db', 'TFSC_OUTPUT_ROOT': str(work / 'tfsc-out'), 'TFSC_CRM_SHARED_SECRET': SECRET,
           'TFSC_CRM_URL': CRM, 'TFSC_REQUIRE_APPROVAL': '1', 'TFSC_RENDERER': 'libreoffice'}
    agreements = ROOT / 'apps' / 'agreements'
    subprocess.run([sys.executable, 'manage.py', 'setup-key'], cwd=agreements, env=env, check=True, capture_output=True)
    cmd = [sys.executable, '-m', 'uvicorn', 'portal:app', '--host', '127.0.0.1', '--port', str(TFSC_PORT)]
    procs.append(subprocess.Popen(cmd, cwd=agreements, env=env, stdout=subprocess.DEVNULL, stderr=open(work / 'tfsc.log', 'w')))  # noqa: S603 - our own app
    wait(f'{TFSC}/api/health')
    procs.append(subprocess.Popen([sys.executable, 'worker.py'], cwd=agreements, env=env, stdout=subprocess.DEVNULL,
                                  stderr=open(work / 'tfsc-worker.log', 'w')))
    return env


class Tfsc:
    def __init__(self) -> None:
        self.h = httpx.Client(base_url=TFSC, timeout=30)
        self.csrf = ''

    def login(self, email: str, pw: str) -> dict:
        r = self.h.post('/api/auth/login', json={'email': email, 'password': pw})
        r.raise_for_status()
        self.csrf = r.json()['csrf']
        return r.json()

    def post(self, path: str, body: dict) -> httpx.Response:
        return self.h.post(path, json=body, headers={'X-CSRF-Token': self.csrf})


def seed(db_url: str, work: Path, agreements: bool) -> dict:
    os.environ.update({'CRM_DATABASE_URL': db_url, 'CRM_MODE': 'local', 'CRM_ORIGIN': 'http://testserver', 'CRM_DATA_DIR': str(work / 'crm-data'),
                       'CRM_INLINE_DISPATCHER': '0', 'CRM_INLINE_SCHEDULER': '0'})
    if agreements:
        os.environ.update({'CRM_TFSC_URL': TFSC, 'CRM_TFSC_BRIDGE_SECRET': SECRET})
    sys.path.insert(0, str(APP))
    from fastapi.testclient import TestClient

    from tfsc_crm.domain import bootstrap
    from tfsc_crm.main import create_app
    from tfsc_crm.security import ratelimit
    app = create_app()
    bootstrap.ensure_setup_key()

    def signed_in(email: str, pw: str = PW) -> TestClient:
        ratelimit.reset_local()
        c = TestClient(app, base_url='http://testserver')
        r = c.post('/api/auth/login', json={'email': email, 'password': pw})
        r.raise_for_status()
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        return c

    root = TestClient(app, base_url='http://testserver')
    r = root.post('/api/auth/setup', json={'setup_key': bootstrap.read_setup_key(), 'name': 'Sara Super', 'email': 'super@e2e.test', 'password': PW})
    r.raise_for_status()
    ids = {'super': r.json()['user']['id']}
    sup_admin = signed_in('super@e2e.test')
    for key, first, last, role, manager, project in USERS:
        temp = 'Temporary-password-2026!'
        body = {'first_name': first, 'last_name': last, 'email': f'{key}@e2e.test', 'crm_role': role, 'manager_id': ids[manager],
                'temporary_password': temp, **({'project_id': project} if project else {})}
        r = sup_admin.post('/api/users', json=body)
        r.raise_for_status()
        ids[key] = r.json()['id']
        u = signed_in(f'{key}@e2e.test', temp)
        u.post('/api/auth/password', json={'current_password': temp, 'new_password': PW}).raise_for_status()
    # Fixed activity for agent4 (reports to sup2). No test touches agent4 or sup2, so their dashboards stay stable for
    # the visual snapshots (dashboards.spec.ts).
    rows = [{'name': f'Snapshot Practice {i:02d} pllc', 'phone': f'(415) 555-{7000 + i:04d}',
             **({'called': 'Yes', 'connected': 'Yes' if i % 3 == 0 else 'No', 'text_sent': 'Yes' if i == 0 else ''} if i < 9 else {})}
            for i in range(12)]
    signed_in('agent4@e2e.test').post('/api/activities/batch', json={'request_key': 'e2e-snapshot-seed', 'rows': rows}).raise_for_status()
    if agreements:
        t = Tfsc()
        key = (work / 'tfsc-data' / 'setup-key.txt').read_text().strip()
        r = t.h.post('/api/auth/setup', json={'setup_key': key, 'name': 'TFSC Root', 'email': TFSC_ROOT[0], 'password': TFSC_ROOT[1]})
        r.raise_for_status()
        t.csrf = r.json()['csrf']
        me = t.h.get('/api/auth/me').json()['user']
        t.post('/api/integrations/crm/actors', {'crm_actor_id': ids['super'].replace('-', ''), 'tfsc_user_id': me['id']}).raise_for_status()
        r = sup_admin.put(f"/api/users/{ids['sup']}/agreements", json={'enabled': True, 'role': 'user', 'companies': ['SCRIBE ALIGN LLC']})
        r.raise_for_status()
        tfsc_uid = r.json()['tfsc_user_id']
        temp = t.post(f'/api/users/{tfsc_uid}', {'reset_password': True}).json()['temporary_password']
        s = Tfsc()
        s.login('sup@e2e.test', temp)
        s.post('/api/auth/password', {'current_password': temp, 'password': PW}).raise_for_status()
    from tfsc_crm.db import session as dbs
    dbs.dispose()
    return ids


def stop(*_: object) -> None:
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    work = Path(tempfile.mkdtemp(prefix='tfsc-e2e-'))
    db_url = recreate_database()
    log(f'database ready; work dir {work}')
    agreements = os.getenv('E2E_AGREEMENTS', '1') != '0' and bool(shutil.which(os.getenv('LIBREOFFICE_BIN', 'soffice')))
    if agreements:
        start_tfsc(work)
        log(f'TFSC Agreements at {TFSC}')
    ids = seed(db_url, work, agreements)
    log(f'seeded {len(ids)} users')
    env = {**os.environ, 'CRM_DATABASE_URL': db_url, 'CRM_MODE': 'local', 'CRM_ORIGIN': CRM, 'CRM_DATA_DIR': str(work / 'crm-data'),
           'CRM_INLINE_DISPATCHER': '1', 'CRM_INLINE_SCHEDULER': '0', 'CRM_SSE_HEARTBEAT_S': '5'}
    env.pop('CRM_REDIS_URL', None)
    cmd = [sys.executable, '-m', 'uvicorn', 'tfsc_crm.main:app', '--host', '127.0.0.1', '--port', str(CRM_PORT), '--workers', '1', '--no-access-log']
    procs.append(subprocess.Popen(cmd, cwd=APP, env=env))  # noqa: S603 - our own app
    wait(f'{CRM}/api/health')
    (ROOT / 'apps' / 'crm-web' / 'e2e' / '.stack.json').write_text(json.dumps({'crm': CRM, 'tfsc': TFSC if agreements else None, 'ids': ids}))
    log(f'CRM at {CRM}; agreements {"on" if agreements else "off (soffice not installed)"}')
    while all(p.poll() is None for p in procs):
        time.sleep(1)
    log('a child process exited; stopping')
    stop()


if __name__ == '__main__':
    main()

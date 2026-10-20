"""Acceptance 12-13 end to end: CRM and TFSC Agreements as separate running sites.

Starts TFSC (API + document worker, real LibreOffice renderer) and a CRM API server as
subprocesses, then: Director/Super Admin grants Agreements access from the CRM (one step),
supervisor without a grant gets 403, supervisor with a grant proceeds from a signed-up lead
(pre-flight + Agreement Lock), TFSC independent review approves, the worker issues documents and
TFSC's signed webhook updates the CRM lead and creates the Client Profile — no manual sync.
"""
import concurrent.futures as cf
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text

from .conftest import PW, create_database, drop_database, new_phone
from .test_api_pipeline import _make_lead

ROOT = Path(__file__).resolve().parents[3]
SECRET = 'e2e-bridge-secret-' + 'z' * 40
pytestmark = pytest.mark.skipif(not shutil.which('soffice'), reason='LibreOffice renderer not installed')


def _port() -> int:
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait(url: str, timeout: float = 40) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            time.sleep(0.3)
    raise AssertionError(f'{url} did not start')


@pytest.fixture(scope='module')
def sites(ws, db_url, tmp_path_factory):
    tport, cport = _port(), _port()
    tdir = tmp_path_factory.mktemp('tfsc')
    tfsc_db = create_database('tfsc_e2e')  # PostgreSQL as in hosted mode (SQLite would serialize the concurrent writes)
    py = sys.executable
    tenv = {**os.environ, 'TFSC_MODE': 'local', 'TFSC_ORIGIN': f'http://127.0.0.1:{tport}', 'TFSC_DATA_DIR': str(tdir / 'data'),
            'DATABASE_URL': tfsc_db, 'TFSC_OUTPUT_ROOT': str(tdir / 'out'), 'TFSC_CRM_SHARED_SECRET': SECRET,
            'TFSC_CRM_URL': f'http://127.0.0.1:{cport}', 'TFSC_REQUIRE_APPROVAL': '1', 'TFSC_RENDERER': 'libreoffice'}
    cenv = {**os.environ, 'CRM_DATABASE_URL': db_url, 'CRM_MODE': 'test', 'CRM_ORIGIN': f'http://127.0.0.1:{cport}', 'CRM_DB_APP_ROLE': 'crm_app',
            'CRM_TFSC_URL': f'http://127.0.0.1:{tport}', 'CRM_TFSC_BRIDGE_SECRET': SECRET, 'CRM_INLINE_DISPATCHER': '0'}
    agreements = ROOT / 'apps' / 'agreements'
    subprocess.run([py, 'manage.py', 'setup-key'], cwd=agreements, env=tenv, check=True, capture_output=True)
    # two workers as deployed (compose/Helm), so concurrent requests really interleave
    procs = [subprocess.Popen([py, '-m', 'uvicorn', 'portal:app', '--host', '127.0.0.1', '--port', str(tport), '--workers', '2'], cwd=agreements, env=tenv,
                              stdout=subprocess.DEVNULL, stderr=open(tdir / 'tfsc.log', 'w')),
             subprocess.Popen([py, '-m', 'uvicorn', 'tfsc_crm.main:app', '--host', '127.0.0.1', '--port', str(cport)], cwd=ROOT / 'apps' / 'crm-api',
                              env=cenv, stdout=subprocess.DEVNULL, stderr=open(tdir / 'crm.log', 'w'))]
    try:
        _wait(f'http://127.0.0.1:{tport}/api/health')
        _wait(f'http://127.0.0.1:{cport}/api/health')
        procs.append(subprocess.Popen([py, 'worker.py'], cwd=agreements, env=tenv, stdout=subprocess.DEVNULL, stderr=open(tdir / 'worker.log', 'w')))
        # in-process CRM test clients talk to the same TFSC
        os.environ.update({'CRM_TFSC_URL': cenv['CRM_TFSC_URL'], 'CRM_TFSC_BRIDGE_SECRET': SECRET})
        from tfsc_crm.config import get_settings
        get_settings.cache_clear()
        yield {'tfsc': f'http://127.0.0.1:{tport}', 'crm': f'http://127.0.0.1:{cport}', 'dir': tdir, 'tfsc_db': tfsc_db}
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(10)
        drop_database(tfsc_db)
        os.environ.pop('CRM_TFSC_URL', None)
        os.environ.pop('CRM_TFSC_BRIDGE_SECRET', None)
        from tfsc_crm.config import get_settings
        get_settings.cache_clear()


class Tfsc:
    def __init__(self, base):
        self.h = httpx.Client(base_url=base, timeout=30)
        self.csrf = ''

    def login(self, email, pw):
        r = self.h.post('/api/auth/login', json={'email': email, 'password': pw})
        assert r.status_code == 200, r.text
        self.csrf = r.json()['csrf']
        return r.json()

    def post(self, path, body, **hdr):
        return self.h.post(path, json=body, headers={'X-CSRF-Token': self.csrf, **hdr})


@pytest.fixture(scope='module')
def tfsc_root(ws, sites):
    tfsc_super = Tfsc(sites['tfsc'])
    key = (sites['dir'] / 'data' / 'setup-key.txt').read_text().strip()
    r = tfsc_super.h.post('/api/auth/setup', json={'setup_key': key, 'name': 'TFSC Root', 'email': 'root@tfsc.test', 'password': PW})
    assert r.status_code == 200, r.text
    tfsc_super.csrf = r.json()['csrf']
    # explicit one-time link of the CRM Super Admin to the TFSC Super Admin (bootstrap of the trust chain)
    me = tfsc_super.h.get('/api/auth/me').json()['user']
    assert tfsc_super.post('/api/integrations/crm/actors', {'crm_actor_id': ws['super'].id.replace('-', ''), 'tfsc_user_id': me['id']}).status_code == 200
    return tfsc_super


def test_grant_handoff_review_issue_client_profile(ws, sites, tfsc_root):
    tfsc_super = tfsc_root

    # 13: Director cannot grant TFSC admin; Super Admin grants TFSC user + Scribe Align only
    assert ws['director'].put(f"/api/users/{ws['sup'].id}/agreements", json={'enabled': True, 'role': 'admin', 'companies': ['SCRIBE ALIGN LLC']}).status_code == 403
    r = ws['super'].put(f"/api/users/{ws['sup'].id}/agreements", json={'enabled': True, 'role': 'user', 'companies': ['SCRIBE ALIGN LLC']})
    assert r.status_code == 200, r.text
    tfsc_uid = r.json()['tfsc_user_id']
    # 12: supervisor without grant -> 403; with grant -> launch available
    assert ws['sup2'].get('/api/agreements/launch').status_code == 403
    sup = type(ws['sup'])(ws['app']).login('sup@crm.test')
    assert sup.get('/api/agreements/launch').json()['companies'] == ['SCRIBE ALIGN LLC']

    _act, _sid, lead = _make_lead(ws, new_phone())
    v = sup.get(f"/api/leads/{lead['id']}").json()['version']
    assert sup.post(f"/api/leads/{lead['id']}/transition", json={'version': v, 'action': 'sign_up'}).status_code == 200
    pre = sup.get(f"/api/leads/{lead['id']}/agreements/preflight").json()
    assert pre['suggested_entity'] == 'SCRIBE ALIGN LLC' and pre['entity_options'] == ['SCRIBE ALIGN LLC']
    body = {'legal_name': pre['legal_name'], 'address': '100 Example Street, New York, NY 10001', 'email': 'owner@practice.test',
            'signatory': 'Pat Owner', 'title': 'Owner', 'effective_date': pre['effective_date'], 'entity': 'EXTENDED HEALTH SERVICES INC.',
            'document_types': ['baa']}
    assert sup.post(f"/api/leads/{lead['id']}/agreements/handoff", json=body).status_code == 403  # company not granted
    body['entity'] = 'SCRIBE ALIGN LLC'
    r = sup.post(f"/api/leads/{lead['id']}/agreements/handoff", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out['status'] == 'opened' and '#redeem=' in out['agreements_url']
    token = out['agreements_url'].split('#redeem=')[1]
    detail = ws['sup'].get(f"/api/leads/{lead['id']}").json()
    assert detail['agreement_locked'] and detail['stage'] == 'agreement_in_progress'
    # Agreement Lock: a supervisor can no longer reassign the lead
    assert ws['sup'].post(f"/api/leads/{lead['id']}/reassign", json={'version': detail['version'], 'to_user': ws['b'].id, 'reason': 'try',
                                                                     'incentive_split': {ws['b'].id: 1.0}}).status_code == 403

    # TFSC: the provisioned supervisor account signs in (temporary password set by TFSC admin), redeems and submits
    t = tfsc_super.post(f'/api/users/{tfsc_uid}', {'reset_password': True}).json()['temporary_password']
    tsup = Tfsc(sites['tfsc'])
    tsup.login(sup.user['email'], t)
    assert tsup.post('/api/auth/password', {'current_password': t, 'password': PW}).status_code == 200
    tsup.login(sup.user['email'], PW)
    red = tsup.post('/api/handoffs/redeem', {'token': token})
    assert red.status_code == 200, red.text
    sub = tsup.post('/api/reviews/submit', {'client_id': red.json()['client_id']})
    assert sub.status_code == 200, sub.text
    review = sub.json()
    # independent review: TFSC Super Admin approves the exact revision
    dec = tfsc_super.post(f"/api/reviews/{review['id']}/decision", {'decision': 'approve', 'version': review['version'], 'reason': 'Checked'})
    assert dec.status_code == 200, dec.text
    gen = tsup.post(f"/api/reviews/{review['id']}/generate", {}, **{'Idempotency-Key': 'e2e-' + review['id']})
    assert gen.status_code == 202, gen.text

    # The worker renders, TFSC emits agreement.issued, the CRM inbox creates the client profile — no manual sync.
    deadline = time.time() + 120
    clients = []
    while time.time() < deadline:
        clients = [c for c in ws['sup'].get('/api/clients').json() if c['lead_id'] == lead['id']]
        if clients:
            break
        time.sleep(1)
    assert clients, (sites['dir'] / 'worker.log').read_text()[-2000:] + (sites['dir'] / 'tfsc.log').read_text()[-2000:]
    prof = ws['sup'].get(f"/api/clients/{clients[0]['id']}").json()
    states = [x['state'] for x in prof['agreements']]
    assert 'approved' in states and 'issued' in states
    issued = next(x for x in prof['agreements'] if x['state'] == 'issued')
    assert issued['files'] and all(f['download_url'].startswith(sites['tfsc'] + '/files/') for f in issued['files'])
    assert ws['sup'].get(f"/api/leads/{lead['id']}").json()['stage'] == 'client'
    # agent sees own client without restricted fields
    agent_view = ws['a'].get(f"/api/clients/{clients[0]['id']}")
    assert agent_view.status_code in (200, 404) and 'tfsc_client_id' not in agent_view.text

    # 13: Super Admin revokes -> TFSC access gone immediately (<= 60 s target)
    t0 = time.time()
    r = ws['super'].put(f"/api/users/{ws['sup'].id}/agreements", json={'enabled': False})
    assert r.status_code == 200, r.text
    assert tsup.h.get('/api/auth/me').status_code == 401
    assert time.time() - t0 < 60


def test_concurrent_same_lead_handoffs(ws, sites, tfsc_root):
    """Legacy CRM-11: parallel Proceed to Agreements on one lead (double-click, two tabs) against a running TFSC."""
    r = ws['super'].put(f"/api/users/{ws['director'].id}/agreements", json={'enabled': True, 'role': 'user', 'companies': ['SCRIBE ALIGN LLC']})
    assert r.status_code == 200, r.text
    tfsc_uid = r.json()['tfsc_user_id']
    director = type(ws['director'])(ws['app']).login('director@crm.test')
    _act, _sid, lead = _make_lead(ws, new_phone())
    v = director.get(f"/api/leads/{lead['id']}").json()['version']
    assert director.post(f"/api/leads/{lead['id']}/transition", json={'version': v, 'action': 'sign_up'}).status_code == 200
    pre = director.get(f"/api/leads/{lead['id']}/agreements/preflight").json()
    body = {'legal_name': pre['legal_name'], 'address': '100 Example Street, New York, NY 10001', 'email': 'owner@practice.test',
            'signatory': 'Pat Owner', 'title': 'Owner', 'effective_date': pre['effective_date'], 'entity': 'SCRIBE ALIGN LLC',
            'document_types': ['baa']}
    url = f"/api/leads/{lead['id']}/agreements/handoff"
    # identical requests (same idempotency key) and different details (different keys), all at once
    bodies = [body] * 8 + [{**body, 'address': f'{n} Example Street, New York, NY 10001'} for n in range(101, 109)]
    with cf.ThreadPoolExecutor(16) as pool:
        out = list(pool.map(lambda b: director.post(url, json=b), bodies))
    assert all(x.status_code == 200 and x.json()['status'] == 'opened' for x in out), [(x.status_code, x.text[:300]) for x in out if x.status_code != 200 or x.json()['status'] != 'opened']

    # CRM: one lock, every request audited, lead consistent
    detail = director.get(f"/api/leads/{lead['id']}").json()
    assert detail['agreement_locked'] and detail['stage'] == 'agreement_in_progress'
    from tfsc_crm.db.session import system_tx
    with system_tx() as c:
        assert c.execute(text("SELECT count(*) FROM audit_log WHERE action = 'agreement.handoff_requested' AND resource_id = :l"),
                         {'l': lead['id']}).scalar_one() == 16
    # TFSC: exactly one draft client for the CRM lead; one handoff per distinct request key
    eng = create_engine(sites['tfsc_db'])
    try:
        with eng.connect() as c:
            links = c.execute(text('SELECT client_id FROM crm_client_links WHERE crm_client_id = :l'), {'l': lead['id']}).all()
            assert len(links) == 1, links
            client_id = links[0][0]
            keys = c.execute(text('SELECT count(DISTINCT request_key), count(*) FROM crm_bridge_handoffs WHERE client_id = :c'), {'c': client_id}).one()
            assert tuple(keys) == (9, 9), keys
    finally:
        eng.dispose()

    # Straight at TFSC (both API workers): 24 parallel signed handoffs for one new CRM client, 6 request keys sent 4 times each
    from tfsc_crm.integrations import tfsc as bridge
    crm_client = str(uuid.uuid4())

    def direct(n: int) -> str:
        k = n % 6
        p = {'crm_client_id': crm_client, 'crm_lead_id': crm_client, 'crm_actor_id': str(ws['director'].id), 'entity': 'SCRIBE ALIGN LLC',
             'client': {'client_name': 'Burst Clinic', 'legal_name': 'Burst Clinic', 'providers': [], 'address': f'{k} Burst Road, Austin, TX 73301',
                        'client_email': '', 'client_phone': '', 'client_signatory': 'Pat Owner', 'client_title': 'Owner'},
             'services': [], 'document_types': ['baa'], 'effective_date': pre['effective_date'], 'domain': '', 'project_owner': '',
             'idempotency_key': f'burst-{crm_client[:8]}-{k}'}
        try:
            return bridge.handoff(p)['tfsc_client_id']
        except bridge.TfscError as e:
            return f'{e.status}: {e}'
    with cf.ThreadPoolExecutor(24) as pool:
        ids = list(pool.map(direct, range(24)))
    assert len(set(ids)) == 1 and len(ids[0]) == 32, ids
    eng = create_engine(sites['tfsc_db'])
    try:
        with eng.connect() as c:
            assert c.execute(text('SELECT count(*) FROM crm_client_links WHERE crm_client_id = :l'), {'l': crm_client}).scalar_one() == 1
            assert c.execute(text('SELECT count(*) FROM crm_bridge_handoffs WHERE client_id = :c'), {'c': ids[0]}).scalar_one() == 6
    finally:
        eng.dispose()

    # The director signs in to TFSC; every distinct handoff redeems to the same client draft
    t = tfsc_root.post(f'/api/users/{tfsc_uid}', {'reset_password': True}).json()['temporary_password']
    tdir = Tfsc(sites['tfsc'])
    tdir.login(director.user['email'], t)
    assert tdir.post('/api/auth/password', {'current_password': t, 'password': PW}).status_code == 200
    tdir.login(director.user['email'], PW)
    tokens = [x.json()['agreements_url'].split('#redeem=')[1] for x in out]
    redeemed = [tdir.post('/api/handoffs/redeem', {'token': tok}) for tok in tokens[8:]]
    assert all(x.status_code == 200 and x.json()['client_id'] == client_id for x in redeemed), [x.text for x in redeemed]
    # identical requests share one handoff whose code rotates: exactly one of those codes still opens it
    same = [tdir.post('/api/handoffs/redeem', {'token': tok}).status_code for tok in tokens[:8]]
    assert sorted(same) == [200] + [404] * 7, same

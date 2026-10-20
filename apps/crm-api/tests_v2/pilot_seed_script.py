"""Creates realistic pilot v1.0.1 data through the pilot API itself (run in a subprocess).

Env: CRM_DATABASE_URL (PostgreSQL at revision 0001), CRM_DATA_DIR. Prints a JSON summary.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('CRM_ORIGIN', 'http://testserver')
os.environ.setdefault('CRM_MODE', 'local')
import crm_app
import crm_db as db
from fastapi.testclient import TestClient

PW = 'Synthetic-test-password-512!'


def login(email):
    c = TestClient(crm_app.app)
    r = c.post('/api/auth/login', json={'email': email, 'password': PW})
    assert r.status_code == 200, r.text
    c.headers['X-CSRF-Token'] = r.json()['csrf']
    return c


with TestClient(crm_app.app) as admin:
    key = (db.DATA / 'setup-key.txt').read_text()
    r = admin.post('/api/auth/setup', json={'setup_key': key, 'name': 'Pilot Super', 'email': 'super@pilot.test', 'password': PW})
    assert r.status_code == 200, r.text
    admin.headers['X-CSRF-Token'] = r.json()['csrf']
    root = r.json()['user']

    def user(name, role, parent):
        r = admin.post('/api/users', json={'name': name, 'email': f'{name}@pilot.test', 'password': PW, 'role': role, 'manager_id': parent, 'reset_required': False})
        assert r.status_code == 200, r.text
        return r.json()

    mgr = user('manager', 'manager', root['id'])
    sup = user('supervisor', 'supervisor', mgr['id'])
    a = user('agenta', 'sales_agent', sup['id'])
    b = user('agentb', 'sales_agent', sup['id'])
    ca, cb, cs = login('agenta@pilot.test'), login('agentb@pilot.test'), login('supervisor@pilot.test')
    acts = []
    acts.append(ca.post('/api/activities', json={'provider_name': 'Research Only Clinic', 'phone': '212-555-0101'}).json())
    acts.append(ca.post('/api/activities', json={'provider_name': 'Called Clinic', 'phone': '212-555-0102', 'called': True, 'connected': True, 'text_sent': True}).json())
    acts.append(ca.post('/api/activities', json={'provider_name': 'Followup Clinic', 'phone': '212-555-0103', 'email': 'Front.Desk@Clinic.test', 'called': True, 'connected': True, 'next_followup': '2026-11-02'}).json())
    acts.append(cb.post('/api/activities', json={'provider_name': 'Followup Clinic dup', 'phone': '(212) 555-0103', 'called': True}).json())
    lead_act = ca.post('/api/activities', json={'provider_name': 'Lead Clinic', 'phone': '212-555-0104', 'called': True, 'connected': True, 'next_followup': '2026-11-03'}).json()
    acts.append(lead_act)
    leads = ca.get('/api/leads').json()['items']
    lead = next(x for x in leads if x['activity_id'] == lead_act['id'])
    r = ca.post(f"/api/leads/{lead['id']}/transition", json={'version': lead['version'], 'action': 'request_strong', 'reason': 'Demo interest'})
    assert r.status_code == 200, r.text
    lead = r.json()
    for action in ('confirm_strong', 'qualify'):
        r = cs.post(f"/api/leads/{lead['id']}/transition", json={'version': lead['version'], 'action': action, 'reason': 'Supervisor reviewed'})
        assert r.status_code == 200, (action, r.text)
        lead = r.json()
    print(json.dumps({'users': 5, 'activities': len(acts), 'lead_stage': lead['stage']}))

"""Seeds a SYNTHETIC demo workspace through the public API (no real provider data).

Usage: CRM_DATABASE_URL=... CRM_DATA_DIR=... python tools/seed_demo.py
Creates: Super Admin, Director, Manager, 2 Supervisors, 8 agents, a group with a leader,
~300 activities across today/this month, follow-ups, strong FUs, leads, a meeting and a sale.
Prints the demo logins (password below is for local demos only).
"""
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('CRM_MODE', 'local')
os.environ.setdefault('CRM_ORIGIN', 'http://testserver')
from fastapi.testclient import TestClient

from tfsc_crm.domain import bootstrap
from tfsc_crm.main import create_app
from tfsc_crm.security import ratelimit

PW = 'Demo-password-2026!'
random.seed(7)
app = create_app()
bootstrap.ensure_setup_key()
FIRST = ['Ava', 'Bilal', 'Carmen', 'Daniyal', 'Elena', 'Faris', 'Grace', 'Hamza', 'Iris', 'Jamal', 'Kiran', 'Leo']
LAST = ['Rahman', 'Lopez', 'Siddiqui', 'Chen', 'Malik', 'Ortiz', 'Qureshi', 'Patel']
PRACTICES = ['Sunrise Pediatrics pa', 'holistic behavioral health llc', 'ALPHA ortho & spine group', 'Bay Area ABA Therapy inc', 'Harbor Family Medicine',
             'Valley Dental Group pc', 'Metro Imaging llc', 'Hope Counseling Services', 'Lakeside Chiropractic', 'Desert Wellness Center', 'Pine Street Clinic',
             'Northgate Psychiatry pllc', 'Coastal Rehab ps', 'Evergreen Urgent Care', 'Summit Surgery Center']


_cache: dict = {}


def client(email=None):
    if email in _cache:
        return _cache[email]
    ratelimit.reset_local()  # the seed signs in many synthetic users from one address
    c = TestClient(app, base_url='http://testserver')
    if email:
        r = c.post('/api/auth/login', json={'email': email, 'password': PW})
        r.raise_for_status()
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        _cache[email] = c
    return c


root = client()
r = root.post('/api/auth/setup', json={'setup_key': bootstrap.read_setup_key(), 'name': 'Sara Super', 'email': 'super@demo.test', 'password': PW})
r.raise_for_status()
root.headers['X-CSRF-Token'] = r.json()['csrf']
ids = {'super': r.json()['user']['id']}


def user(key, first, last, role, manager, creator='super', **kw):
    c = client('super@demo.test') if creator == 'super' else client(f'{creator}@demo.test')
    r = c.post('/api/users', json={'first_name': first, 'last_name': last, 'email': f'{key}@demo.test', 'crm_role': role, 'manager_id': ids[manager], 'temporary_password': PW, **kw})
    r.raise_for_status()
    ids[key] = r.json()['id']
    ratelimit.reset_local()
    u = client()
    t = u.post('/api/auth/login', json={'email': f'{key}@demo.test', 'password': PW})
    u.headers['X-CSRF-Token'] = t.json()['csrf']
    u.post('/api/auth/password', json={'current_password': PW, 'new_password': PW + '#'}).raise_for_status()
    t = u.post('/api/auth/login', json={'email': f'{key}@demo.test', 'password': PW + '#'})
    u.headers['X-CSRF-Token'] = t.json()['csrf']
    u.post('/api/auth/password', json={'current_password': PW + '#', 'new_password': PW}).raise_for_status()


user('director', 'Daniel', 'Director', 'director_admin', 'super')
user('manager', 'Mona', 'Manager', 'manager', 'director')
user('sup', 'Samir', 'Supervisor', 'supervisor', 'manager', project_id='P0007')
user('sup2', 'Selena', 'Supervisor', 'supervisor', 'manager')
agents = []
for i in range(8):
    key = f'agent{i + 1}'
    user(key, FIRST[i], LAST[i], 'sales_agent', 'sup' if i < 5 else 'sup2', project_id='P0007' if i < 5 else 'P0010')
    agents.append(key)
sup = client('sup@demo.test')
sup.post('/api/teams', json={'name': 'Group Falcon', 'kind': 'group', 'supervisor_id': ids['sup'], 'member_ids': [ids['agent1'], ids['agent2'], ids['agent3']], 'leader_id': ids['agent1']}).raise_for_status()
phones = [f'(415) 555-{2000 + i:04d}' for i in range(400)]
made = []
for i, key in enumerate(agents):
    c = client(f'{key}@demo.test')
    rows = []
    for j in range(random.randint(20, 45)):
        called = random.random() < 0.85
        conn = called and random.random() < 0.35
        rows.append({'name': random.choice(PRACTICES) if random.random() < 0.6 else f'dr {random.choice(FIRST).lower()} {random.choice(LAST).lower()} md',
                     'phone': phones[(i * 50 + j) % len(phones)], 'called': 'Yes' if called else ('No' if random.random() < 0.5 else ''),
                     'connected': 'Yes' if conn else ('No' if called else ''), 'text_sent': 'Yes' if conn and random.random() < 0.4 else '',
                     'next_followup': '2026-10-27' if conn and random.random() < 0.3 else '', 'remarks': 'Asked to call back next week' if conn else ''})
    out = c.post('/api/activities/batch', json={'request_key': f'demo-seed-{key}', 'rows': rows}).json()
    made += [(key, r['activity_id']) for r in out['rows'] if r['status'] == 'saved']
# strong follow-ups -> leads -> meeting -> sale
for key, aid in made[:40]:
    c = client(f'{key}@demo.test')
    a = c.get(f'/api/activities/{aid}').json()
    if a.get('connected') and not a.get('conflict'):
        r = c.post(f'/api/activities/{aid}/strong', json={'target_window': '2-3 weeks', 'note': 'Interested in billing support demo'})
        if r.status_code == 200:
            s = client('sup@demo.test') if key in agents[:5] else client('sup2@demo.test')
            sid = r.json()['id']
            v = next((x for x in s.get('/api/strong').json() if x['id'] == sid), None)
            if v and random.random() < 0.7:
                s.post(f'/api/strong/{sid}/decide', json={'version': v['version'], 'decision': 'confirmed'})
                if random.random() < 0.6:
                    lead = s.post(f'/api/strong/{sid}/convert', json={}).json()
                    if random.random() < 0.5 and 'id' in lead:
                        d = s.get(f"/api/leads/{lead['id']}").json()
                        s.post(f"/api/leads/{lead['id']}/meetings", json={'title': 'Billing demo', 'starts_at': '2026-10-22T17:00:00Z', 'duration_minutes': 30})
                        d = s.get(f"/api/leads/{lead['id']}").json()
                        if random.random() < 0.5:
                            s.post(f"/api/leads/{lead['id']}/transition", json={'version': d['version'], 'action': 'sign_up'})
print('Demo seeded. Logins (password', PW + '):', ', '.join(f'{k}@demo.test' for k in ['super', 'director', 'manager', 'sup', 'sup2', *agents]))

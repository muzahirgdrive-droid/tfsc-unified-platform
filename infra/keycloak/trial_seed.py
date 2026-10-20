"""LOCAL TRIALS ONLY (compose stack with `--profile trial`): bootstrap the CRM and TFSC Super Admins, link them, and
invite synthetic users so Keycloak emails them (Mailpit). Then run apps/crm-web/e2e/sso-invites.mjs.

  python infra/keycloak/trial_seed.py state.json setup     # after the stack is up (and `manage.py setup-key` in agreements)
  node apps/crm-web/e2e/sso-invites.mjs invites out/       # invitations, CRM SSO (+TOTP), password recovery
  python infra/keycloak/trial_seed.py state.json grant     # Agreements grants (one before the person's first SSO sign-in)
  node apps/crm-web/e2e/sso-invites.mjs tfsc out/          # TFSC SSO for both
The first `setup` run needs the CRM setup key: it bootstraps the CRM Super Admin from /data/setup-key.txt.
"""
import json
import os
import subprocess
import sys

import httpx

PW = 'Trial-password-2026!x'
CRM, TFSC = os.getenv('TRIAL_CRM', 'http://crm.localhost:8766'), os.getenv('TRIAL_TFSC', 'http://tfsc.localhost:8765')
PROJECT = os.getenv('TRIAL_PROJECT', 'tfsc-trial')


def client(origin, port):
    host = origin.split('//')[1]
    return httpx.Client(base_url=f'http://127.0.0.1:{port}', headers={'Origin': origin, 'Host': host}, timeout=30)
def dc(*args):
    return subprocess.run(['docker', 'compose', '-p', PROJECT, *args], capture_output=True, text=True, check=True).stdout  # noqa: S603,S607
state_path = sys.argv[1]
step = sys.argv[2]
state = json.load(open(state_path)) if step != 'setup' else {}
crm = client(CRM, 8766)
def crm_login():
    r = crm.post('/api/auth/login', json={'email': 'super@trial.test', 'password': PW}); r.raise_for_status()
    crm.headers['X-CSRF-Token'] = r.json()['csrf']
if step == 'setup':
    r = crm.post('/api/auth/setup', json={'setup_key': dc('exec', '-T', 'crm-api', 'cat', '/data/setup-key.txt').strip(), 'name': 'Trial Super',
                                          'email': 'super@trial.test', 'password': PW})
    if r.status_code == 200:
        crm.headers['X-CSRF-Token'] = r.json()['csrf']
    else:  # already bootstrapped by an earlier run
        crm_login()
    state['crm_super'] = crm.get('/api/auth/me').json()['user']['id']
    tf = client(TFSC, 8765)
    tkey = dc('exec', '-T', 'agreements', 'cat', '/data/setup-key.txt').strip()
    r = tf.post('/api/auth/setup', json={'setup_key': tkey, 'name': 'TFSC Root', 'email': 'root@trial.test', 'password': PW}); r.raise_for_status()
    tf.headers['X-CSRF-Token'] = r.json()['csrf']
    me = tf.get('/api/auth/me').json()['user']
    r = tf.post('/api/integrations/crm/actors', json={'crm_actor_id': state['crm_super'].replace('-', ''), 'tfsc_user_id': me['id']}); r.raise_for_status()
    print('status', crm.get('/api/auth/status').json())
    for key_, role in (('sup', 'supervisor'), ('agent', 'sales_agent')):
        body = {'first_name': key_.capitalize(), 'last_name': 'Trial', 'email': f'{key_}@trial.test', 'crm_role': role,
                'manager_id': state.get('sup', state['crm_super'])}
        r = crm.post('/api/users', json=body); r.raise_for_status()
        state[key_] = r.json()['id']; print(key_, r.json()['status'])
elif step == 'grant':
    crm_login()
    for key_ in ('sup',):
        r = crm.put(f"/api/users/{state[key_]}/agreements", json={'enabled': True, 'role': 'user', 'companies': ['SCRIBE ALIGN LLC']}); print('grant', key_, r.status_code, r.text[:160])
    r = crm.post('/api/users', json={'first_name': 'Late', 'last_name': 'Trial', 'email': 'late@trial.test', 'crm_role': 'sales_agent', 'manager_id': state['sup']}); r.raise_for_status()
    state['late'] = r.json()['id']
    r = crm.put(f"/api/users/{state['late']}/agreements", json={'enabled': True, 'role': 'user', 'companies': ['SCRIBE ALIGN LLC']}); print('grant late (before first SSO)', r.status_code, r.text[:160])
elif step == 'report':
    crm_login()
    for key_ in [k for k in ('sup', 'agent', 'late') if k in state]:
        u = crm.get(f"/api/users/{state[key_]}").json(); print(key_, u['status'])
json.dump(state, open(state_path, 'w'))

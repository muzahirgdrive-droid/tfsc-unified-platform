"""LOCAL TRIALS ONLY: create Keycloak users for a compose trial (never run against production).

Usage: python infra/keycloak/dev_seed.py http://localhost:8081 admin <admin-password> users.json
users.json: [{"email": "...", "first": "...", "last": "...", "password": "...", "mfa": true, "totp_secret": "..."}]
Users with "mfa" join the supervisors-and-above group (TOTP required). A "totp_secret" pre-registers an authenticator
so automated end-to-end tests can sign in; real users enrol their own device on first sign-in.
"""
import json
import sys
import urllib.parse
import urllib.request

base, admin, password, path = sys.argv[1].rstrip('/'), sys.argv[2], sys.argv[3], sys.argv[4]
if urllib.parse.urlsplit(base).scheme not in ('http', 'https'):
    raise SystemExit('The Keycloak URL must be http(s).')


def call(method: str, url: str, token: str | None = None, body: dict | None = None, form: dict | None = None) -> dict | None:
    data = urllib.parse.urlencode(form).encode() if form else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - scheme checked above
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded' if form else 'application/json')
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - scheme checked above
        raw = r.read()
    return json.loads(raw) if raw else None


tok = call('POST', f'{base}/realms/master/protocol/openid-connect/token',
           form={'grant_type': 'password', 'client_id': 'admin-cli', 'username': admin, 'password': password})['access_token']
users = []
for u in json.load(open(path)):
    creds = [{'type': 'password', 'value': u['password'], 'temporary': False}]
    if u.get('totp_secret'):
        creds.append({'type': 'otp', 'userLabel': 'e2e', 'secretData': json.dumps({'value': u['totp_secret']}),
                      'credentialData': json.dumps({'subType': 'totp', 'digits': 6, 'counter': 0, 'period': 30, 'algorithm': 'HmacSHA1'})})
    users.append({'username': u['email'], 'email': u['email'], 'emailVerified': True, 'enabled': True, 'firstName': u['first'],
                  'lastName': u['last'], 'credentials': creds, 'groups': ['/supervisors-and-above'] if u.get('mfa') else ['/agents']})
res = call('POST', f'{base}/admin/realms/tfsc/partialImport', tok, {'ifResourceExists': 'OVERWRITE', 'users': users})
print('keycloak users:', res.get('overwritten', 0) + res.get('added', 0) if res else 0)

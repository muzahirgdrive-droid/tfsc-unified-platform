"""Keycloak invitations and account lifecycle (§4.2, §6): invite email via Keycloak (set password; TOTP for
Supervisor+), group by role, disable on suspension/offboarding, resend permissions, invited -> active on first SSO
sign-in. Keycloak's admin API is an in-memory fake here; the real email path is verified on the compose trial stack."""
import json
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import text

from .conftest import PW
from .test_oidc import ISS, _begin, _callback, _claims, idp  # noqa: F401 - idp is a fixture

ORIGIN = 'http://testserver'


class FakeKeycloak:
    def __init__(self):
        self.users: dict[str, dict] = {}
        self.groups = {'/supervisors-and-above': 'g-mfa', '/agents': 'g-agents'}
        self.membership: dict[str, set[str]] = {}
        self.emails: list[dict] = []
        self.logouts: list[str] = []

    def __call__(self, method, url, **kw):
        u = urlsplit(url)
        path, q = u.path, {k: v[0] for k, v in parse_qs(u.query).items()} | {k: str(v) for k, v in (kw.get('params') or {}).items()}
        assert path.startswith('/realms/tfsc/protocol') or kw['headers']['Authorization'] == 'Bearer kc-token'
        res = self.route(method, path.removeprefix('/admin/realms/tfsc'), q, kw.get('json'))
        status, body = res if isinstance(res, tuple) else (200, res)
        return httpx.Response(status, content=b'' if body is None else json.dumps(body).encode(), request=httpx.Request(method, url))

    def route(self, method, path, q, body):
        parts = path.strip('/').split('/')
        if path == '/realms/tfsc/protocol/openid-connect/token':
            return {'access_token': 'kc-token', 'expires_in': 300}
        if path == '/users' and method == 'GET':
            return [x for x in self.users.values() if x['email'] == q['email']]
        if path == '/users' and method == 'POST':
            if any(x['email'] == body['email'] for x in self.users.values()):
                return 409, {'errorMessage': 'exists'}
            uid = str(uuid.uuid4())
            self.users[uid] = {**body, 'id': uid}
            return 201, None
        if path == '/groups':
            return [{'id': gid, 'path': p} for p, gid in self.groups.items()]
        uid = parts[1]
        if len(parts) == 2:
            if method == 'GET':
                return self.users[uid]
            self.users[uid] = body
            return 204, None
        if parts[2] == 'groups':
            (self.membership.setdefault(uid, set()).add if method == 'PUT' else self.membership.setdefault(uid, set()).discard)(parts[3])
            return 204, None
        if parts[2] == 'execute-actions-email':
            self.emails.append({'user': uid, 'actions': body, **q})
            return 204, None
        if parts[2] == 'logout':
            self.logouts.append(uid)
            return 204, None
        raise AssertionError(f'unexpected {method} {path}')

    def by_email(self, email):
        return next(x for x in self.users.values() if x['email'] == email)


@pytest.fixture
def kc(ws, monkeypatch):
    from tfsc_crm.config import get_settings
    from tfsc_crm.integrations import keycloak
    fake = FakeKeycloak()
    monkeypatch.setenv('CRM_OIDC_ISSUER', ISS)
    monkeypatch.setenv('CRM_OIDC_CLIENT_ID', 'tfsc-crm')
    monkeypatch.setenv('CRM_KEYCLOAK_CLIENT_SECRET', 'provisioner-secret')
    monkeypatch.setenv('CRM_ORIGIN', ORIGIN)
    get_settings.cache_clear()
    keycloak._token.clear()
    monkeypatch.setattr(keycloak, '_request', fake)
    yield fake
    get_settings.cache_clear()


def _deliver(user_id) -> list[str]:
    """Run the outbox consumer for this person's sso.* events only (other tests' events stay untouched)."""
    from tfsc_crm.db.session import system_tx
    from tfsc_crm.workers import dispatcher
    with system_tx() as c:
        rows = [dict(r) for r in c.execute(text("SELECT * FROM outbox WHERE key = :k AND topic LIKE 'sso.%' AND state = 'pending' ORDER BY id"),
                                           {'k': str(user_id)}).mappings()]
        for row in rows:
            dispatcher._deliver(row)
            c.execute(text("UPDATE outbox SET state = 'delivered' WHERE id = :i"), {'i': row['id']})
    return [r['topic'] for r in rows]


def _create(ws, creator, email, role, **extra):
    r = ws[creator].post('/api/users', json={'first_name': 'Ivy', 'last_name': 'Invite', 'email': email, 'crm_role': role,
                                             'manager_id': ws['sup'].id if role == 'sales_agent' else ws['manager'].id, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_without_provisioning_nothing_is_queued(ws):
    assert ws['super'].get('/api/auth/status').json()['invitations'] is False
    u = _create(ws, 'manager', 'plain.invite@crm.test', 'sales_agent')
    assert u['status'] == 'invited' and _deliver(u['id']) == []
    assert ws['manager'].post(f"/api/users/{u['id']}/invite").status_code == 503


def test_invitation_groups_and_disable(ws, kc):
    assert ws['super'].get('/api/auth/status').json()['invitations'] is True
    agent = _create(ws, 'manager', 'ivy.agent@crm.test', 'sales_agent')
    assert _deliver(agent['id']) == ['sso.invite.requested']
    acct = kc.by_email('ivy.agent@crm.test')
    assert acct['enabled'] and not acct['emailVerified'] and kc.membership[acct['id']] == {'g-agents'}
    [mail] = kc.emails
    assert mail['actions'] == ['UPDATE_PASSWORD'] and mail['client_id'] == 'tfsc-crm'
    assert mail['redirect_uri'] == f'{ORIGIN}/login' and mail['lifespan'] == str(72 * 3600)

    sup = _create(ws, 'director', 'ivy.sup@crm.test', 'supervisor')
    assert _deliver(sup['id']) == ['sso.invite.requested']
    assert kc.emails[-1]['actions'] == ['UPDATE_PASSWORD', 'CONFIGURE_TOTP']  # Supervisor+: TOTP enrolment in the same email
    assert kc.membership[kc.by_email('ivy.sup@crm.test')['id']] == {'g-mfa'}

    # a temporary password means a local (break-glass) account: no invitation
    local = _create(ws, 'manager', 'ivy.local@crm.test', 'sales_agent', temporary_password=PW)
    assert local['status'] == 'active' and _deliver(local['id']) == []

    # resend: Manager+ in scope only
    assert ws['sup'].post(f"/api/users/{agent['id']}/invite").status_code == 403
    assert ws['manager'].post(f"/api/users/{uuid.uuid4()}/invite").status_code == 404
    assert ws['manager'].post(f"/api/users/{agent['id']}/invite").status_code == 200
    assert _deliver(agent['id']) == ['sso.invite.requested'] and len(kc.emails) == 3
    assert len([u for u in kc.users.values() if u['email'] == 'ivy.agent@crm.test']) == 1  # found, not duplicated

    # promotion moves the person into the TOTP group; suspension disables the SSO account and ends its sessions
    v = ws['director'].get(f"/api/users/{agent['id']}").json()['version']
    assert ws['director'].patch(f"/api/users/{agent['id']}", json={'version': v, 'crm_role': 'supervisor'}).status_code == 200
    assert _deliver(agent['id']) == ['sso.user.sync']
    aid = acct['id']
    assert kc.membership[aid] == {'g-mfa'}
    v = ws['director'].get(f"/api/users/{agent['id']}").json()['version']
    assert ws['director'].patch(f"/api/users/{agent['id']}", json={'version': v, 'status': 'suspended'}).status_code == 200
    _deliver(agent['id'])
    assert kc.users[aid]['enabled'] is False and kc.users[aid]['email'] == 'ivy.agent@crm.test' and aid in kc.logouts

    # offboarding disables the account too; a leaver cannot be re-invited
    off = ws['director'].post(f"/api/users/{sup['id']}/offboard", json={'reason': 'Left'})
    assert off.status_code == 200, off.text
    assert _deliver(sup['id']) == ['sso.user.disable']
    sid = kc.by_email('ivy.sup@crm.test')['id']
    assert kc.users[sid]['enabled'] is False and sid in kc.logouts
    assert ws['director'].post(f"/api/users/{sup['id']}/invite").status_code == 409


def test_keycloak_failure_retries_through_the_outbox(ws, kc, monkeypatch):
    from tfsc_crm.integrations import keycloak
    u = _create(ws, 'manager', 'ivy.retry@crm.test', 'sales_agent')
    monkeypatch.setattr(keycloak, '_request', lambda *a, **k: httpx.Response(503, request=httpx.Request('GET', 'http://kc')))
    keycloak._token.clear()
    with pytest.raises(keycloak.KeycloakError):
        _deliver(u['id'])  # raises, so the dispatcher keeps the event pending and retries it with backoff (no fake success)


def test_first_sso_sign_in_activates_the_invited_user(ws, idp):  # noqa: F811
    u = _create(ws, 'manager', 'ivy.first@crm.test', 'sales_agent')
    assert u['status'] == 'invited'
    c, q = _begin(ws['app'], '/')
    idp['claims'] = _claims(q, sub='kc-ivy-first', email='ivy.first@crm.test', email_verified=True)
    r = _callback(c, q)
    assert r.status_code == 302 and r.headers['location'] == '/', r.headers.get('location')
    assert ws['manager'].get(f"/api/users/{u['id']}").json()['status'] == 'active'
    from tfsc_crm.db.session import system_tx
    with system_tx() as conn:
        assert conn.execute(text("SELECT count(*) FROM audit_log WHERE action = 'user.activated' AND resource_id = :u"), {'u': u['id']}).scalar_one() == 1
    # a suspended person stays out even with a valid identity
    v = ws['manager'].get(f"/api/users/{u['id']}").json()['version']
    assert ws['manager'].patch(f"/api/users/{u['id']}", json={'version': v, 'status': 'suspended'}).status_code == 200
    c2, q2 = _begin(ws['app'], '/')
    idp['claims'] = _claims(q2, sub='kc-ivy-first', email='ivy.first@crm.test')
    assert 'sso_error=inactive' in _callback(c2, q2).headers['location']


def test_first_sso_sign_in_sends_tfsc_the_identity_for_an_existing_grant(ws, idp):  # noqa: F811
    from tfsc_crm.db.session import system_tx
    u = _create(ws, 'manager', 'ivy.granted@crm.test', 'sales_agent')
    with system_tx() as conn:  # granted Agreements before ever signing in with SSO
        conn.execute(text("""INSERT INTO module_grants (id, org_id, user_id, module, role, companies, external_account_id, status, granted_by)
                             SELECT gen_random_uuid(), org_id, id, 'agreements', 'user', '["SCRIBE ALIGN LLC"]'::jsonb, 'tfsc-acct-1', 'active',
                                    (SELECT id FROM users WHERE email = 'super@crm.test') FROM users WHERE id = :u"""), {'u': u['id']})
    c, q = _begin(ws['app'], '/')
    idp['claims'] = _claims(q, sub='kc-ivy-granted', email='ivy.granted@crm.test')
    assert _callback(c, q).headers['location'] == '/'
    with system_tx() as conn:
        rows = conn.execute(text("SELECT payload FROM outbox WHERE topic = 'account.provision.requested' AND key = :k"), {'k': u['id']}).scalars().all()
    assert len(rows) == 1
    p = rows[0]
    assert (p['action'], p['oidc_issuer'], p['oidc_subject'], p['companies']) == ('grant', ISS, 'kc-ivy-granted', ['SCRIBE ALIGN LLC'])
    # a later sign-in (identity already linked) sends nothing new
    c2, q2 = _begin(ws['app'], '/')
    idp['claims'] = _claims(q2, sub='kc-ivy-granted', email='ivy.granted@crm.test')
    _callback(c2, q2)
    with system_tx() as conn:
        assert conn.execute(text("SELECT count(*) FROM outbox WHERE topic = 'account.provision.requested' AND key = :k"), {'k': u['id']}).scalar_one() == 1

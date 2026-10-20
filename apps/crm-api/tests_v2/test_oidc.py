"""OIDC sign-in (Keycloak) against a fake provider with real RS256-signed ID tokens: PKCE, state, nonce, audience,
expiry, signature, MFA for Supervisor+, no auto-provisioning, identity linking, open-redirect safety."""
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

ISS = 'https://sso.test/realms/tfsc'
CLIENT = 'tfsc-crm'


@pytest.fixture
def idp(ws, monkeypatch):
    from joserfc import jwt
    from joserfc.jwk import KeySet, RSAKey

    from tfsc_crm.config import get_settings
    from tfsc_crm.security import oidc
    key = RSAKey.generate_key(2048, parameters={'kid': 'k1', 'use': 'sig', 'alg': 'RS256'})
    rogue = RSAKey.generate_key(2048, parameters={'kid': 'k1', 'use': 'sig', 'alg': 'RS256'})
    jwks = KeySet([key]).as_dict(private=False)
    disco = {'issuer': ISS, 'authorization_endpoint': ISS + '/protocol/openid-connect/auth', 'token_endpoint': ISS + '/protocol/openid-connect/token',
             'jwks_uri': ISS + '/protocol/openid-connect/certs'}
    state: dict = {'claims': {}, 'key': key, 'posts': []}

    def fake_get(url):
        return disco if url.endswith('/.well-known/openid-configuration') else jwks

    def fake_post(url, data, auth):
        state['posts'].append((url, data, auth))
        return {'id_token': jwt.encode({'alg': 'RS256', 'kid': 'k1'}, state['claims'], state['key']), 'access_token': 'x'}

    monkeypatch.setenv('CRM_OIDC_ISSUER', ISS)
    monkeypatch.setenv('CRM_OIDC_CLIENT_ID', CLIENT)
    monkeypatch.setenv('CRM_OIDC_CLIENT_SECRET', 'test-secret')
    get_settings.cache_clear()
    oidc._discovery.clear()
    oidc._jwks.clear()
    monkeypatch.setattr(oidc, 'http_get', fake_get)
    monkeypatch.setattr(oidc, 'http_post', fake_post)
    state['rogue'] = rogue
    yield state
    monkeypatch.delenv('CRM_OIDC_ISSUER')
    get_settings.cache_clear()
    oidc._discovery.clear()
    oidc._jwks.clear()


def _begin(app, next_path='/leads'):
    c = TestClient(app, base_url='http://testserver')
    r = c.get('/api/auth/oidc/start', params={'next': next_path}, follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = urlsplit(r.headers['location'])
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    assert loc.geturl().startswith(ISS + '/protocol/openid-connect/auth')
    assert q['code_challenge_method'] == 'S256' and len(q['code_challenge']) == 43 and q['client_id'] == CLIENT
    assert q['redirect_uri'] == 'http://testserver/api/auth/oidc/callback' and 'openid' in q['scope']
    return c, q


def _claims(q, **over):
    now = int(time.time())
    base = {'iss': ISS, 'sub': 'kc-sub-b', 'aud': CLIENT, 'azp': CLIENT, 'exp': now + 300, 'iat': now, 'nonce': q['nonce'],
            'email': 'b@crm.test', 'email_verified': True, 'acr': '1'}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def _callback(c, q, state=None):
    return c.get('/api/auth/oidc/callback', params={'code': 'auth-code', 'state': state or q['state']}, follow_redirects=False)


def _sso_error(r) -> str | None:
    assert r.status_code == 302
    loc = r.headers['location']
    return parse_qs(urlsplit(loc).query).get('sso_error', [None])[0] if loc.startswith('/login') else None


def test_status_advertises_sso(ws, idp):
    assert TestClient(ws['app'], base_url='http://testserver').get('/api/auth/status').json()['oidc'] is True


def test_agent_signs_in_and_identity_is_linked(ws, idp):
    c, q = _begin(ws['app'])
    idp['claims'] = _claims(q)
    r = _callback(c, q)
    assert _sso_error(r) is None and r.headers['location'] == '/leads'
    assert c.get('/api/auth/me').json()['user']['email'] == 'b@crm.test'
    _url, data, auth = idp['posts'][-1]
    assert data['code_verifier'] and data['grant_type'] == 'authorization_code' and auth == (CLIENT, 'test-secret')
    # Next time the subject alone identifies the user, even if the email claim changed.
    c2, q2 = _begin(ws['app'], '/')
    idp['claims'] = _claims(q2, email='renamed@elsewhere.test')
    assert _sso_error(_callback(c2, q2)) is None
    assert c2.get('/api/auth/me').json()['user']['email'] == 'b@crm.test'


@pytest.mark.parametrize(('change', 'reason'), [
    ({'nonce': 'replayed-nonce'}, 'token'),
    ({'aud': 'another-client'}, 'token'),
    ({'iss': 'https://evil.test/realms/tfsc'}, 'token'),
    ({'exp': int(time.time()) - 3600, 'iat': int(time.time()) - 7200}, 'token'),
    ({'sub': 'kc-unknown', 'email': 'nobody@crm.test'}, 'unknown_user'),
    ({'sub': 'kc-unverified', 'email': 'c@crm.test', 'email_verified': False}, 'unknown_user'),
])
def test_bad_tokens_are_refused(ws, idp, change, reason):
    c, q = _begin(ws['app'])
    idp['claims'] = _claims(q, **change)
    assert _sso_error(_callback(c, q)) == reason
    assert c.get('/api/auth/me').status_code == 401


def test_forged_signature_state_mismatch_and_missing_cookie(ws, idp):
    c, q = _begin(ws['app'])
    idp['claims'] = _claims(q)
    idp['key'] = idp['rogue']
    assert _sso_error(_callback(c, q)) == 'token'
    c, q = _begin(ws['app'])
    assert _sso_error(_callback(c, q, state='not-the-state')) == 'state'
    fresh = TestClient(ws['app'], base_url='http://testserver')
    assert _sso_error(fresh.get('/api/auth/oidc/callback', params={'code': 'x', 'state': q['state']}, follow_redirects=False)) == 'state'


def test_supervisor_needs_mfa(ws, idp):
    c, q = _begin(ws['app'])
    idp['claims'] = _claims(q, sub='kc-sup', email='sup@crm.test', acr='1', amr=['pwd'])
    assert _sso_error(_callback(c, q)) == 'mfa_required'
    c, q = _begin(ws['app'])
    idp['claims'] = _claims(q, sub='kc-sup', email='sup@crm.test', acr='1', amr=['pwd', 'otp'])
    assert _sso_error(_callback(c, q)) is None
    assert c.get('/api/auth/me').json()['user']['role'] == 'supervisor'


def test_next_is_never_an_open_redirect(ws, idp):
    for bad in ('//evil.example/x', 'https://evil.example', '/api/auth/logout', '\\\\evil'):
        c, q = _begin(ws['app'], bad)
        idp['claims'] = _claims(q)
        assert _callback(c, q).headers['location'] == '/'


def test_sso_disabled_by_default(ws):
    c = TestClient(ws['app'], base_url='http://testserver')
    assert c.get('/api/auth/oidc/start', follow_redirects=False).status_code == 404
    assert c.get('/api/auth/status').json()['oidc'] is False


def test_sso_removes_an_unused_temporary_password(ws, idp):
    from sqlalchemy import text

    from tfsc_crm.db.session import system_tx
    r = ws['manager'].post('/api/users', json={'first_name': 'Temp', 'last_name': 'Pass', 'email': 'temp-sso@crm.test', 'crm_role': 'sales_agent',
                                               'manager_id': ws['sup'].id, 'temporary_password': 'Temporary-password-2026!'})
    assert r.status_code == 200, r.text
    c, q = _begin(ws['app'])
    idp['claims'] = _claims(q, sub='kc-temp', email='temp-sso@crm.test')
    assert _sso_error(_callback(c, q)) is None
    assert c.get('/api/auth/me').json()['user']['reset_required'] is False
    with system_tx() as conn:
        row = conn.execute(text("SELECT password_hash IS NULL, reset_required FROM users WHERE email = 'temp-sso@crm.test'")).one()
    assert tuple(row) == (True, False)

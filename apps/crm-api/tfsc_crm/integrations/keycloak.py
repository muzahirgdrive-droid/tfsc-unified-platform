"""Keycloak account lifecycle for CRM users (§4.2, §6, ADR-0014): the invitation email (set a password, enrol TOTP for
Supervisor and above), group membership by role, and disabling the account at suspension/offboarding.

- Keycloak sends the email itself (realm SMTP settings, its own templates and signed action tokens); the CRM never
  sees or mails a password or link. Completing the invitation verifies the email, so the first SSO sign-in links the
  identity and activates the invited CRM user.
- Calls go through the outbox (topics `sso.*`), so a Keycloak or SMTP outage only delays them; failures retry and end
  in the Security Center's dead letters.
- Auth: client-credentials of a confidential service-account client in the CRM's realm (CRM_KEYCLOAK_CLIENT_ID /
  CRM_KEYCLOAK_CLIENT_SECRET; realm-management roles manage-users, view-users, query-users, query-groups). The admin
  API base and realm come from CRM_OIDC_INTERNAL_URL (else CRM_OIDC_ISSUER): `<base>/realms/<realm>`.
- Not configured -> `configured()` is False and nothing is queued; the UI says invitations are off (no fake success).
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import httpx

from ..config import get_settings

TOPICS = {'sso.invite.requested', 'sso.user.sync', 'sso.user.disable'}
GROUP_MFA, GROUP_AGENTS = '/supervisors-and-above', '/agents'
_token: dict[str, Any] = {}


class KeycloakError(Exception):
    pass


def _realm_url() -> tuple[str, str]:
    s = get_settings()
    issuer = s.oidc_internal_url or s.oidc_issuer
    base, sep, realm = issuer.partition('/realms/')
    if not sep or not realm:
        raise KeycloakError('CRM_OIDC_ISSUER must look like https://sso.example.com/realms/<realm>.')
    return base.rstrip('/'), realm.strip('/')


def _request(method: str, url: str, **kw: Any) -> httpx.Response:
    return httpx.request(method, url, **kw)


def configured() -> bool:
    s = get_settings()
    return bool(s.oidc_issuer and s.keycloak_client_secret)


def _access_token() -> str:
    if _token.get('exp', 0) > time.time() + 30:
        return str(_token['value'])
    s = get_settings()
    base, realm = _realm_url()
    r = _request('POST', f'{base}/realms/{realm}/protocol/openid-connect/token', timeout=15,
                 data={'grant_type': 'client_credentials', 'client_id': s.keycloak_client_id, 'client_secret': s.keycloak_client_secret})
    if r.status_code != 200:
        raise KeycloakError(f'Keycloak token request failed ({r.status_code}).')
    body = r.json()
    _token.update(value=body['access_token'], exp=time.time() + int(body.get('expires_in', 60)))
    return str(body['access_token'])


def _api(method: str, path: str, **kw: Any) -> httpx.Response:
    base, realm = _realm_url()
    r = _request(method, f'{base}/admin/realms/{realm}{path}', headers={'Authorization': f'Bearer {_access_token()}'}, timeout=20, **kw)
    if r.status_code >= 400 and r.status_code != 409:
        raise KeycloakError(f'Keycloak {method} {path.split("?")[0]} failed ({r.status_code}): {r.text[:200]}')
    return r


def find_user(email: str) -> dict | None:
    users = _api('GET', '/users', params={'email': email, 'exact': 'true'}).json()
    return next((u for u in users if str(u.get('email', '')).lower() == email.lower()), None)


def _ensure_user(p: dict) -> str:
    u = find_user(p['email'])
    if u:
        return str(u['id'])
    _api('POST', '/users', json={'username': p['email'], 'email': p['email'], 'firstName': p.get('first_name') or '',
                                 'lastName': p.get('last_name') or '', 'enabled': True, 'emailVerified': False})
    u = find_user(p['email'])
    if not u:
        raise KeycloakError('Keycloak did not return the new user.')
    return str(u['id'])


def _update(user_id: str, **changes: Any) -> None:
    """PUT the full representation: a partial one can clear user-profile attributes."""
    rep = _api('GET', f'/users/{user_id}').json()
    _api('PUT', f'/users/{user_id}', json={**rep, **changes})


def _group_ids() -> dict[str, str]:
    return {g['path']: g['id'] for g in _api('GET', '/groups', params={'briefRepresentation': 'true'}).json()}


def _set_groups(user_id: str, mfa: bool) -> None:
    ids = _group_ids()
    want, other = (GROUP_MFA, GROUP_AGENTS) if mfa else (GROUP_AGENTS, GROUP_MFA)
    if want not in ids:
        raise KeycloakError(f'Keycloak group {want} is missing from the realm.')
    _api('PUT', f'/users/{user_id}/groups/{ids[want]}')
    if other in ids:
        _api('DELETE', f'/users/{user_id}/groups/{ids[other]}')


def invite(p: dict) -> None:
    s = get_settings()
    uid = _ensure_user(p)
    _set_groups(uid, bool(p.get('mfa')))
    _update(uid, enabled=True)
    actions = ['UPDATE_PASSWORD'] + (['CONFIGURE_TOTP'] if p.get('mfa') else [])
    query = f'client_id={quote(s.oidc_client_id)}&redirect_uri={quote(s.origin + "/login", safe="")}&lifespan={s.invite_lifespan_hours * 3600}'
    _api('PUT', f'/users/{uid}/execute-actions-email?{query}', json=actions)


def sync(p: dict) -> None:
    """Role or status change: group by role, enabled by status. Accounts that were never invited are left alone."""
    u = find_user(p['email'])
    if not u:
        return
    _set_groups(str(u['id']), bool(p.get('mfa')))
    _update(str(u['id']), enabled=bool(p.get('enabled', True)))
    if not p.get('enabled', True):
        _api('POST', f"/users/{u['id']}/logout")


def disable(p: dict) -> None:
    u = find_user(p['email'])
    if u:
        _update(str(u['id']), enabled=False)
        _api('POST', f"/users/{u['id']}/logout")  # ends their SSO sessions too


def deliver(row: dict) -> None:
    """Outbox consumer for sso.* topics."""
    if not configured():
        raise KeycloakError('Keycloak provisioning is not configured (CRM_KEYCLOAK_CLIENT_SECRET).')
    {'sso.invite.requested': invite, 'sso.user.sync': sync, 'sso.user.disable': disable}[row['topic']](dict(row['payload']))

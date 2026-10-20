"""OIDC sign-in with Keycloak (§21, ADR-0014): authorization code flow with PKCE (S256).

- The ID token is validated against the provider's JWKS: signature (RS256/ES256), issuer, audience, expiry, nonce, azp.
- State, nonce and the PKCE verifier travel in a short-lived HMAC-signed, HttpOnly cookie bound to this browser.
- No auto-provisioning: the person must already exist in the CRM (created by a manager), be active, and is matched by
  (issuer, subject) or, on first sign-in, by a verified email, which then links the identity.
- MFA is Keycloak's job; the CRM refuses Supervisor+ (mfa_required) sign-ins whose token does not show it
  (`acr` in CRM_OIDC_MFA_ACR, or an MFA method in `amr`).
- Back-channel calls (token, JWKS) can go to an internal URL (CRM_OIDC_INTERNAL_URL) while the browser and the
  `iss` claim use the public issuer, as in Docker/Kubernetes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import get_settings

COOKIE = 'crm_oidc'
TTL_S = 600
MFA_AMR = {'otp', 'totp', 'hwk', 'swk', 'mfa', 'webauthn'}
_discovery: dict[str, Any] = {}
_jwks: dict[str, Any] = {}
_secret_fallback = secrets.token_bytes(32)


class OidcError(Exception):
    def __init__(self, code: str, detail: str = ''):
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True)
class Provider:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None


def enabled() -> bool:
    s = get_settings()
    return bool(s.oidc_issuer and s.oidc_client_id)


def _backchannel(url: str) -> str:
    s = get_settings()
    if s.oidc_internal_url and url.startswith(s.oidc_issuer):
        return s.oidc_internal_url.rstrip('/') + url[len(s.oidc_issuer):]
    return url


def http_get(url: str) -> dict:  # replaced in tests
    r = httpx.get(_backchannel(url), timeout=5)
    r.raise_for_status()
    return r.json()


def http_post(url: str, data: dict, auth: tuple[str, str]) -> dict:  # replaced in tests
    r = httpx.post(_backchannel(url), data=data, auth=auth, timeout=10)
    if r.status_code >= 400:
        raise OidcError('token_exchange', f'Token endpoint returned {r.status_code}')
    return r.json()


def provider() -> Provider:
    s = get_settings()
    now = time.time()
    if not _discovery or _discovery.get('_at', 0) < now - 3600 or _discovery.get('_issuer') != s.oidc_issuer:
        d = http_get(s.oidc_issuer.rstrip('/') + '/.well-known/openid-configuration')
        if d.get('issuer') != s.oidc_issuer:
            raise OidcError('discovery', 'Discovery issuer does not match CRM_OIDC_ISSUER.')
        _discovery.clear()
        _discovery.update(d, _at=now, _issuer=s.oidc_issuer)
    return Provider(_discovery['issuer'], _discovery['authorization_endpoint'], _discovery['token_endpoint'], _discovery['jwks_uri'],
                    _discovery.get('end_session_endpoint'))


def _keyset(force: bool = False):
    from joserfc.jwk import KeySet
    now = time.time()
    if force or not _jwks or _jwks.get('_at', 0) < now - 3600:
        _jwks.clear()
        _jwks.update(keys=KeySet.import_key_set(http_get(provider().jwks_uri)), _at=now)  # type: ignore[arg-type]
    return _jwks['keys']


def _secret() -> bytes:
    s = get_settings()
    base = s.oidc_state_secret or s.field_key or s.phone_key_secret
    return hashlib.sha256(('oidc-state:' + base).encode()).digest() if base else _secret_fallback


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def _sign(payload: dict) -> str:
    body = _b64(json.dumps(payload, separators=(',', ':')).encode())
    return body + '.' + _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())


def _unsign(value: str) -> dict:
    try:
        body, sig = value.rsplit('.', 1)
        if not hmac.compare_digest(sig, _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
    except Exception:
        raise OidcError('state', 'The sign-in request could not be verified.') from None
    if data.get('exp', 0) < time.time():
        raise OidcError('expired', 'The sign-in request expired. Try again.')
    return data


def safe_next(path: str | None) -> str:
    """Only same-site relative paths (no open redirects)."""
    if not path or not path.startswith('/') or path.startswith('//') or '\\' in path or path.startswith('/api/'):
        return '/'
    return path[:300]


def redirect_uri() -> str:
    return get_settings().origin + '/api/auth/oidc/callback'


def start(next_path: str | None) -> tuple[str, str]:
    """Returns (authorization URL, signed cookie value)."""
    p = provider()
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    params = {'response_type': 'code', 'client_id': get_settings().oidc_client_id, 'redirect_uri': redirect_uri(),
              'scope': 'openid email profile', 'state': state, 'nonce': nonce, 'code_challenge': challenge, 'code_challenge_method': 'S256'}
    url = p.authorization_endpoint + '?' + httpx.QueryParams(params).__str__()
    return url, _sign({'s': state, 'n': nonce, 'v': verifier, 'next': safe_next(next_path), 'exp': int(time.time()) + TTL_S})


def finish(code: str, state: str, cookie: str | None) -> tuple[dict, str]:
    """Exchange the code and validate the ID token. Returns (claims, next path)."""
    if not cookie:
        raise OidcError('state', 'The sign-in session was not found in this browser.')
    data = _unsign(cookie)
    if not hmac.compare_digest(str(data.get('s', '')), state or ''):
        raise OidcError('state', 'The sign-in response does not match this browser.')
    s = get_settings()
    p = provider()
    tokens = http_post(p.token_endpoint, {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri(),
                                          'code_verifier': data['v'], 'client_id': s.oidc_client_id}, (s.oidc_client_id, s.oidc_client_secret))
    raw = tokens.get('id_token')
    if not raw:
        raise OidcError('token', 'The identity provider returned no ID token.')
    claims = validate_id_token(raw, data['n'])
    return claims, data.get('next') or '/'


def validate_id_token(raw: str, nonce: str) -> dict:
    from joserfc import jwt
    from joserfc.errors import JoseError
    from joserfc.jwt import JWTClaimsRegistry
    s = get_settings()
    try:
        try:
            tok = jwt.decode(raw, _keyset(), algorithms=['RS256', 'ES256', 'PS256'])
        except JoseError:
            tok = jwt.decode(raw, _keyset(force=True), algorithms=['RS256', 'ES256', 'PS256'])  # key rotation
        JWTClaimsRegistry(leeway=60, iss={'essential': True, 'value': s.oidc_issuer}, sub={'essential': True},
                          exp={'essential': True}, iat={'essential': True}).validate(tok.claims)
    except JoseError as e:
        raise OidcError('token', f'ID token rejected: {type(e).__name__}') from None
    c = tok.claims
    aud = c.get('aud')
    if s.oidc_client_id not in (aud if isinstance(aud, list) else [aud]):
        raise OidcError('token', 'ID token audience mismatch.')
    if isinstance(aud, list) and len(aud) > 1 and c.get('azp') != s.oidc_client_id:
        raise OidcError('token', 'ID token azp mismatch.')
    if not hmac.compare_digest(str(c.get('nonce', '')), nonce):
        raise OidcError('token', 'ID token nonce mismatch.')
    return dict(c)


def mfa_satisfied(claims: dict) -> bool:
    acr_ok = {a.strip() for a in get_settings().oidc_mfa_acr.split(',') if a.strip()}
    amr = claims.get('amr') or []
    return str(claims.get('acr', '')) in acr_ok or bool(MFA_AMR & {str(x).lower() for x in amr})

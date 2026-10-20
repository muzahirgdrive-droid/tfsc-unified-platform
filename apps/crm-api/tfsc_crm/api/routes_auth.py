"""Health, server time / world clock, authentication and sessions."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import func, select, text, update

from ..config import get_settings
from ..db import models as m
from ..db.session import system_tx, tx
from ..domain import audit, bootstrap
from ..ids import uuid7
from ..pacific import utcnow, world_clock
from ..security import ratelimit
from ..security.auth import COOKIE, build_actor, create_session, hash_password, revoke_session, verify_password
from ..security.roles import ROLE_LABEL, Actor
from .deps import ActorDep, ResetActorDep, client_ip
from .schemas import AuthStatus, Health, LoginIn, OkOut, PasswordIn, SessionOut, SetupIn, TimeOut, UserMe

router = APIRouter(prefix='/api', tags=['auth'])


def me_out(a: Actor, has_photo: bool, reset_required: bool) -> UserMe:
    return UserMe(id=a.id, display_name=a.display_name, email=a.email, role=a.role, role_label=ROLE_LABEL.get(a.role, a.role),
                  employee_code=a.employee_code, capabilities=a.capabilities(), is_group_leader=a.is_group_leader,
                  agreements_access=a.can('agreements.open'), has_photo=has_photo, reset_required=reset_required)


@router.get('/health', response_model=Health)
def health() -> Health:
    s = get_settings()
    try:
        with system_tx() as c:
            rev = c.execute(text('SELECT version_num FROM alembic_version')).scalar_one_or_none() or 'unmigrated'
    except Exception:
        rev = 'unavailable'
    return Health(app='TFSC Sales CRM', version='2.0.0-dev', mode=s.mode, database=rev)


@router.get('/time', response_model=TimeOut)
def server_time() -> TimeOut:
    return TimeOut.model_validate(world_clock())


@router.get('/auth/status', response_model=AuthStatus)
def auth_status() -> AuthStatus:
    from ..config import get_settings as gs
    from ..security import oidc
    return AuthStatus(setup_required=bootstrap.setup_required(), local_login=gs().allow_local_login, oidc=oidc.enabled())


def _session(request: Request, response: Response, user: dict) -> SessionOut:
    with system_tx() as c:
        csrf, token_hash = create_session(c, user['id'], request, response)
        c.execute(update(m.users).where(m.users.c.id == user['id']).values(last_login_at=utcnow()))
        a = build_actor(c, user, csrf, token_hash)
    return SessionOut(user=me_out(a, bool(user.get('photo_object_key')), bool(user.get('reset_required'))), csrf=csrf)


@router.post('/auth/setup', response_model=SessionOut)
def setup(body: SetupIn, request: Request, response: Response) -> SessionOut:
    ratelimit.enforce(f'setup:{client_ip(request)}', 5, 900)
    key = bootstrap.read_setup_key()
    if not bootstrap.setup_required() or not key or not secrets.compare_digest(key, body.setup_key):
        raise HTTPException(403, 'Setup is not available or the setup key is wrong.')
    with system_tx() as c:
        org_id = c.execute(select(m.orgs.c.id).order_by(m.orgs.c.created_at).limit(1)).scalar_one_or_none()
        if org_id is None:
            raise HTTPException(503, 'Database is not migrated. Run alembic upgrade head.')
    uid = uuid7()
    parts = body.name.split(' ', 1)
    with tx(org_id, uid) as c:
        c.execute(m.users.insert().values(id=uid, org_id=org_id, email=body.email, display_name=body.name, first_name=parts[0],
                                          last_name=parts[1] if len(parts) > 1 else None, crm_role='super_admin', status='active',
                                          password_hash=hash_password(body.password), mfa_required=True))
        audit.record(c, org_id, uid, 'auth.setup', 'user', uid, None, {'email': body.email})
    bootstrap.consume_setup_key()
    with system_tx() as c:
        user = dict(c.execute(select(m.users).where(m.users.c.id == uid)).mappings().one())
    return _session(request, response, user)


@router.post('/auth/login', response_model=SessionOut)
def login(body: LoginIn, request: Request, response: Response) -> SessionOut:
    ip = client_ip(request)
    ratelimit.enforce(f'login-ip:{ip}', 30, 900)
    ratelimit.enforce(f'login-user:{body.email}', 10, 900)
    with system_tx() as c:
        user = c.execute(select(m.users).where(m.users.c.email == body.email)).mappings().first()
    ok = user is not None and verify_password(user['password_hash'], body.password) and user['status'] == 'active'
    if user and user['crm_role'] != 'super_admin' and not get_settings().allow_local_login:
        ok = False
    if not ok:
        if user:
            with tx(user['org_id'], None) as c:
                audit.record(c, user['org_id'], user['id'], 'auth.login_failed', 'user', user['id'], None, {'ip': ip}, ip=ip)
        raise HTTPException(401, 'Email or password is incorrect.')
    assert user is not None
    out = _session(request, response, dict(user))
    with tx(user['org_id'], user['id']) as c:
        audit.record(c, user['org_id'], user['id'], 'auth.login', 'user', user['id'], None, None, ip=ip, ua=request.headers.get('user-agent'))
    return out


@router.get('/auth/oidc/start', include_in_schema=False)
def oidc_start(request: Request, next: str | None = None) -> Response:
    """Begin SSO: redirect to Keycloak with PKCE; state/nonce/verifier ride in a signed, short-lived cookie."""
    from fastapi.responses import RedirectResponse

    from ..security import oidc
    if not oidc.enabled():
        raise HTTPException(404, 'Single sign-on is not configured.')
    ratelimit.enforce(f'oidc-start:{client_ip(request)}', 60, 900)
    try:
        url, cookie = oidc.start(next)
    except (oidc.OidcError, Exception):
        return RedirectResponse('/login?sso_error=unavailable', status_code=302)
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(oidc.COOKIE, cookie, httponly=True, secure=get_settings().hosted, samesite='lax', max_age=oidc.TTL_S, path='/api/auth/oidc')
    return resp


@router.get('/auth/oidc/callback', include_in_schema=False)
def oidc_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None) -> Response:
    """Finish SSO. Every failure lands on /login with a short reason code (details stay in the audit log)."""
    from fastapi.responses import RedirectResponse

    from ..security import oidc

    def fail(reason: str, user: dict | None = None, detail: str = '') -> Response:
        if user:
            with tx(user['org_id'], None) as c:
                audit.record(c, user['org_id'], user['id'], 'auth.login_failed', 'user', user['id'], None,
                             {'method': 'oidc', 'reason': reason, 'detail': detail[:200]}, ip=client_ip(request))
        r = RedirectResponse(f'/login?sso_error={reason}', status_code=302)
        r.delete_cookie(oidc.COOKIE, path='/api/auth/oidc')
        return r

    if not oidc.enabled():
        raise HTTPException(404, 'Single sign-on is not configured.')
    ratelimit.enforce(f'oidc-cb:{client_ip(request)}', 60, 900)
    if error or not code or not state:
        return fail('cancelled')
    try:
        claims, next_path = oidc.finish(code, state, request.cookies.get(oidc.COOKIE))
    except oidc.OidcError as e:
        return fail(e.code)
    except Exception:
        return fail('unavailable')
    iss, sub = claims['iss'], str(claims['sub'])
    with system_tx() as c:
        uid = c.execute(select(m.oidc_identities.c.user_id).where(m.oidc_identities.c.issuer == iss, m.oidc_identities.c.subject == sub)).scalar_one_or_none()
        user = None
        if uid:
            user = c.execute(select(m.users).where(m.users.c.id == uid)).mappings().first()
        elif claims.get('email') and claims.get('email_verified') is True:
            user = c.execute(select(m.users).where(func.lower(m.users.c.email) == str(claims['email']).lower())).mappings().first()
            if user:
                linked = c.execute(select(m.oidc_identities.c.subject).where(m.oidc_identities.c.issuer == iss,
                                                                         m.oidc_identities.c.user_id == user['id'])).first()
                if linked:  # this CRM user is already bound to a different identity at this issuer
                    return fail('identity_mismatch', dict(user))
                c.execute(m.oidc_identities.insert().values(issuer=iss, subject=sub, user_id=user['id']))
    if not user:
        return fail('unknown_user')
    u = dict(user)
    if u['status'] != 'active' or u['crm_role'] == 'none':
        return fail('inactive', u)
    if (u['mfa_required'] or u['crm_role'] in ('supervisor', 'manager', 'director_admin', 'super_admin')) and not oidc.mfa_satisfied(claims):
        return fail('mfa_required', u, f"acr={claims.get('acr')} amr={claims.get('amr')}")
    if u['reset_required']:
        # A temporary password was never used: with SSO it is not needed, so remove it rather than force a change.
        with system_tx() as c:
            c.execute(update(m.users).where(m.users.c.id == u['id']).values(password_hash=None, reset_required=False))
        u.update(password_hash=None, reset_required=False)
    resp = RedirectResponse(next_path, status_code=302)
    _session(request, resp, u)
    resp.delete_cookie(oidc.COOKIE, path='/api/auth/oidc')
    with tx(u['org_id'], u['id']) as c:
        audit.record(c, u['org_id'], u['id'], 'auth.login', 'user', u['id'], None, {'method': 'oidc', 'acr': claims.get('acr'), 'amr': claims.get('amr')},
                     ip=client_ip(request), ua=request.headers.get('user-agent'))
    return resp


@router.post('/auth/logout', response_model=OkOut)
def logout(request: Request, response: Response) -> OkOut:
    token = request.cookies.get(COOKIE)
    if token:
        revoke_session(token)
    response.delete_cookie(COOKIE, path='/')
    return OkOut()


@router.get('/auth/me', response_model=SessionOut)
def me(a: ResetActorDep) -> SessionOut:
    """Current user plus the session's CSRF token (same-origin JS only; the session cookie is HttpOnly)."""
    with system_tx() as c:
        u = c.execute(select(m.users.c.photo_object_key, m.users.c.reset_required).where(m.users.c.id == a.id)).one()
    return SessionOut(user=me_out(a, bool(u[0]), bool(u[1])), csrf=a.csrf)


@router.post('/auth/password', response_model=OkOut)
def change_password(body: PasswordIn, a: ResetActorDep, request: Request) -> OkOut:
    ratelimit.enforce(f'pw:{a.id}', 10, 900)
    with tx(a.org_id, a.id) as c:
        stored = c.execute(select(m.users.c.password_hash).where(m.users.c.id == a.id)).scalar_one()
        if not verify_password(stored, body.current_password):
            raise HTTPException(400, 'Current password is incorrect.')
        c.execute(update(m.users).where(m.users.c.id == a.id).values(password_hash=hash_password(body.new_password), reset_required=False))
        c.execute(update(m.user_sessions).where(m.user_sessions.c.user_id == a.id, m.user_sessions.c.token_hash != a.session_hash).values(revoked_at=utcnow()))
        audit.record(c, a.org_id, a.id, 'auth.password_changed', 'user', a.id)
    return OkOut()


@router.get('/config')
def client_config(a: ActorDep) -> dict:
    """Non-sensitive settings every signed-in user needs (dialer mode, celebration timing, labels)."""
    from ..db.session import read
    from ..domain import settings as cfg
    with read(a.org_id, a.id) as c:
        dialer = cfg.get(c, a.org_id, 'dialer') or {}
        cel = cfg.get(c, a.org_id, 'celebration') or {}
    template = dialer.get('template') if dialer.get('mode') == 'template' and dialer.get('verified') else None
    from ..integrations import tfsc
    return {'dialer': {'mode': dialer.get('mode', 'tel') if template or dialer.get('mode') != 'template' else 'tel', 'template': template},
            'celebration_seconds': int(cel.get('seconds', 6)), 'agreements_url': tfsc.public_url() if a.can('agreements.open') else None,
            'business_timezone': 'America/Los_Angeles'}


@router.get('/auth/sessions')
def my_sessions(a: ActorDep) -> list[dict]:
    with system_tx() as c:
        rows = c.execute(select(m.user_sessions.c.token_hash, m.user_sessions.c.created_at, m.user_sessions.c.last_seen_at,
                                m.user_sessions.c.ip, m.user_sessions.c.user_agent).where(
            m.user_sessions.c.user_id == a.id, m.user_sessions.c.revoked_at.is_(None), m.user_sessions.c.expires_at > utcnow())).mappings().all()
    return [{'id': r['token_hash'][:16], 'current': r['token_hash'] == a.session_hash, 'created_at': r['created_at'].isoformat(),
             'last_seen_at': r['last_seen_at'].isoformat(), 'ip': str(r['ip']) if r['ip'] else None, 'user_agent': r['user_agent']} for r in rows]


@router.delete('/auth/sessions/{sid}', response_model=OkOut)
def revoke(sid: str, a: ActorDep) -> OkOut:
    if len(sid) != 16:
        raise HTTPException(404, 'Session not found.')
    with system_tx() as c:
        c.execute(update(m.user_sessions).where(m.user_sessions.c.user_id == a.id, m.user_sessions.c.token_hash.like(sid + '%')).values(revoked_at=utcnow()))
    return OkOut()

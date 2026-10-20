"""Sessions (hashed tokens, HttpOnly cookie, CSRF header), Argon2id passwords, rate limits."""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, Response
from sqlalchemy import Connection, select, update

from ..config import get_settings
from ..db import models as m
from ..db.session import system_tx
from ..pacific import utcnow
from .roles import Actor

COOKIE = 'crm_session'
hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


def digest(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def hash_password(value: str) -> str:
    if not isinstance(value, str) or not 12 <= len(value) <= 200:
        raise HTTPException(400, 'Use a password with 12 to 200 characters.')
    return hasher.hash(value)


def verify_password(stored: str | None, password: str) -> bool:
    if not stored:
        return False
    try:
        return hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


def _ip(request: Request) -> str | None:
    import ipaddress
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def create_session(c: Connection, user_id, request: Request, response: Response) -> tuple[str, str]:
    s = get_settings()
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = utcnow()
    c.execute(m.user_sessions.delete().where(m.user_sessions.c.expires_at < now))
    c.execute(m.user_sessions.insert().values(
        token_hash=digest(token), user_id=user_id, csrf=csrf, created_at=now, last_seen_at=now,
        expires_at=now + timedelta(hours=s.session_absolute_hours), idle_expires_at=now + timedelta(minutes=s.session_idle_minutes),
        ip=_ip(request), user_agent=(request.headers.get('user-agent') or '')[:300]))
    response.set_cookie(COOKIE, token, httponly=True, secure=s.hosted, samesite='lax', max_age=s.session_absolute_hours * 3600, path='/')
    return csrf, digest(token)


def revoke_session(token: str) -> None:
    with system_tx() as c:
        c.execute(update(m.user_sessions).where(m.user_sessions.c.token_hash == digest(token)).values(revoked_at=utcnow()))


def load_actor(request: Request, allow_reset: bool = False) -> Actor:
    """Authenticate the request (cookie + CSRF for mutations). Runs as the owner role
    because the org is not yet known; returns an Actor used to scope every later query."""
    token = request.cookies.get(COOKIE, '')
    if not token:
        raise HTTPException(401, 'Please sign in.')
    s = get_settings()
    now = utcnow()
    with system_tx() as c:
        row = c.execute(select(m.user_sessions).where(
            m.user_sessions.c.token_hash == digest(token), m.user_sessions.c.revoked_at.is_(None),
            m.user_sessions.c.expires_at > now, m.user_sessions.c.idle_expires_at > now)).mappings().first()
        if not row:
            raise HTTPException(401, 'Your session has expired. Please sign in again.')
        u = c.execute(select(m.users).where(m.users.c.id == row['user_id'])).mappings().first()
        if not u or u['status'] != 'active':
            raise HTTPException(401, 'Your account is not active.')
        if (now - row['last_seen_at']).total_seconds() > 60:
            c.execute(update(m.user_sessions).where(m.user_sessions.c.token_hash == row['token_hash']).values(
                last_seen_at=now, idle_expires_at=now + timedelta(minutes=s.session_idle_minutes)))
        actor = build_actor(c, dict(u), row['csrf'], row['token_hash'])
    if u['reset_required'] and not allow_reset:
        raise HTTPException(403, 'Change your temporary password before continuing.')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and not secrets.compare_digest(request.headers.get('x-csrf-token', ''), row['csrf']):
        raise HTTPException(403, 'Reload the page before submitting this request.')
    return actor


def build_actor(c: Connection, u: dict, csrf: str, token_hash: str) -> Actor:
    groups = [r[0] for r in c.execute(select(m.teams.c.id).where(
        m.teams.c.leader_id == u['id'], m.teams.c.kind == 'group', m.teams.c.active.is_(True)))]
    grant = c.execute(select(m.module_grants.c.role, m.module_grants.c.companies, m.module_grants.c.external_account_id).where(
        m.module_grants.c.user_id == u['id'], m.module_grants.c.module == 'agreements', m.module_grants.c.revoked_at.is_(None),
        m.module_grants.c.status == 'active')).mappings().first()
    return Actor(id=u['id'], org_id=u['org_id'], role=u['crm_role'], display_name=u['display_name'], email=u['email'],
                 routing_view=u['routing_view'], led_group_ids=groups, agreements_grant=dict(grant) if grant else None,
                 employee_code=u['employee_code'], csrf=csrf, session_hash=token_hash)

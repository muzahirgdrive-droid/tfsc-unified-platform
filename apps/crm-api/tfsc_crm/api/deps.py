"""Shared API dependencies and helpers."""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request

from ..security.auth import load_actor
from ..security.roles import Actor


def current_actor(request: Request) -> Actor:
    a = load_actor(request)
    if a.role == 'none':
        raise HTTPException(403, 'Your account has no access to the sales site.')
    return a


def current_actor_allow_reset(request: Request) -> Actor:
    return load_actor(request, allow_reset=True)


ActorDep = Annotated[Actor, Depends(current_actor)]
ResetActorDep = Annotated[Actor, Depends(current_actor_allow_reset)]


def encode_cursor(*parts: Any) -> str:
    return base64.urlsafe_b64encode(json.dumps([p.isoformat() if isinstance(p, datetime) else str(p) for p in parts]).encode()).decode()


def decode_cursor(cursor: str | None) -> list[str] | None:
    if not cursor:
        return None
    try:
        v = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        assert isinstance(v, list)
        return v
    except Exception:
        raise HTTPException(400, 'Invalid page cursor.') from None


def parse_uuid(v: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError:
        raise HTTPException(404, 'Record not found.') from None


def client_ip(request: Request) -> str | None:
    import ipaddress
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None

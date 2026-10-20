"""CRM -> TFSC Agreements client (signed bridge, packages/bridge-contract)."""
from __future__ import annotations

import os
from typing import Any

import httpx

from tfsc_bridge import BridgeError, canonical_body, headers

from ..config import get_settings


class TfscError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def configured() -> bool:
    s = get_settings()
    return bool(s.tfsc_url and len(s.bridge_secret) >= 32)


def public_url() -> str:
    return get_settings().tfsc_url


def _base() -> str:
    return (os.getenv('CRM_TFSC_INTERNAL_URL') or get_settings().tfsc_url).rstrip('/')


def post(path: str, payload: dict[str, Any], *, event_id: str | None = None, schema: str | None = None, timeout: float = 15) -> dict:
    if not configured():
        raise TfscError(503, 'TFSC Agreements is not connected (set CRM_TFSC_URL and CRM_TFSC_BRIDGE_SECRET).')
    body = canonical_body(payload)
    try:
        h = headers(get_settings().bridge_secret, 'POST', path, body, event_id=event_id, schema=schema)
    except BridgeError as e:
        raise TfscError(e.status, str(e)) from None
    # The Host header must match TFSC's public origin even when an internal URL is used.
    from urllib.parse import urlsplit
    h['Host'] = urlsplit(get_settings().tfsc_url).netloc
    try:
        r = httpx.post(_base() + path, content=body, headers=h, timeout=timeout)
    except httpx.HTTPError as e:
        raise TfscError(503, f'TFSC Agreements is unreachable: {e.__class__.__name__}') from None
    try:
        data = r.json()
    except ValueError:
        data = {'error': r.text[:300]}
    if r.status_code >= 400:
        raise TfscError(r.status_code, str(data.get('error') or data.get('detail') or data)[:500])
    return data


def provision(payload: dict) -> dict:
    return post('/api/integrations/v1/crm/accounts', payload, schema='account_provision_request@1')


def handoff(payload: dict) -> dict:
    return post('/api/integrations/v1/crm/handoffs', payload, schema='handoff_request@2')


def deliver(row: dict) -> None:
    """Outbox consumer for TFSC-bound topics (retried with backoff by the dispatcher)."""
    p = dict(row['payload'])
    p.pop('schema_version', None)
    event_id = p.pop('event_id', None)
    if row['topic'] == 'agreement.handoff.requested':
        handoff({k: v for k, v in p.items() if k != 'audience'})
    elif row['topic'] in ('account.provision.requested', 'user.offboarded'):
        provision({k: v for k, v in p.items() if k != 'audience'})
    else:  # pragma: no cover - guarded by dispatcher
        raise TfscError(400, f'No TFSC route for {row["topic"]} ({event_id})')

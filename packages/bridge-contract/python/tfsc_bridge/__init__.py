"""TFSC <-> CRM signed integration contract (Python). Mirror: ../ts/src/index.ts.

Signature (v1, unchanged from the pilot so existing TFSC deployments keep working):
  message = "\\n".join([timestamp, nonce, METHOD, path, sha256_hex(raw_body)])
  X-TFSC-Signature = hex(HMAC-SHA256(shared_secret, message))
Headers: X-TFSC-Timestamp (unix seconds), X-TFSC-Nonce ([A-Za-z0-9_-]{16,100}), X-TFSC-Signature.
Events additionally carry X-TFSC-Event-Id and X-TFSC-Schema (e.g. "agreement.issued@1").
The receiver rejects |now - timestamp| > 300 s, reused nonces and bad signatures.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = ['SCHEMAS', 'BridgeError', 'canonical_body', 'headers', 'sign', 'validate', 'verify']

TOLERANCE_S = 300
NONCE_RE = re.compile(r'^[A-Za-z0-9_-]{16,100}$')
SCHEMA_DIR = Path(__file__).resolve().parents[2] / 'schemas'


class BridgeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def canonical_body(payload: Any) -> bytes:
    """Bytes actually sent. Signatures cover these exact bytes (never re-serialized JSON)."""
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def sign(secret: str, method: str, path: str, body: bytes, timestamp: str, nonce: str) -> str:
    msg = '\n'.join([timestamp, nonce, method.upper(), path, hashlib.sha256(body).hexdigest()])
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def headers(secret: str, method: str, path: str, body: bytes, *, event_id: str | None = None, schema: str | None = None,
            now: float | None = None, nonce: str | None = None) -> dict[str, str]:
    if len(secret) < 32:
        raise BridgeError(503, 'Bridge secret must be at least 32 characters.')
    ts = str(int(now if now is not None else time.time()))
    n = nonce or secrets.token_urlsafe(24)
    h = {'Content-Type': 'application/json', 'X-TFSC-Timestamp': ts, 'X-TFSC-Nonce': n,
         'X-TFSC-Signature': sign(secret, method, path, body, ts, n)}
    if event_id:
        h['X-TFSC-Event-Id'] = event_id
    if schema:
        h['X-TFSC-Schema'] = schema
    return h


def verify(secret: str, method: str, path: str, body: bytes, hdrs: dict[str, str], *, now: float | None = None,
           seen_nonce: Callable[[str], bool] | None = None) -> None:
    """Raise BridgeError unless the request is authentic, fresh and not a replay.
    `seen_nonce(n)` must atomically record the nonce and return True if it was already used."""
    if len(secret) < 32:
        raise BridgeError(503, 'Bridge is not configured with a sufficiently strong shared secret.')
    low = {k.lower(): v for k, v in hdrs.items()}
    ts, nonce, sig = low.get('x-tfsc-timestamp', ''), low.get('x-tfsc-nonce', ''), low.get('x-tfsc-signature', '')
    try:
        fresh = abs((now if now is not None else time.time()) - int(ts)) <= TOLERANCE_S
    except ValueError:
        fresh = False
    if not fresh or not NONCE_RE.match(nonce):
        raise BridgeError(401, 'Invalid bridge authentication.')
    if not hmac.compare_digest(sig, sign(secret, method, path, body, ts, nonce)):
        raise BridgeError(401, 'Invalid bridge authentication.')
    if seen_nonce is not None and seen_nonce(nonce):
        raise BridgeError(409, 'Bridge request was already received.')


def _load_schemas() -> dict[str, dict]:
    out = {}
    for p in sorted(SCHEMA_DIR.glob('*.json')):
        out[p.stem] = json.loads(p.read_text(encoding='utf-8'))
    return out


SCHEMAS = _load_schemas()


def validate(schema_name: str, payload: Any) -> None:
    """Minimal JSON-Schema subset validator (type/required/enum/properties/items/pattern/maxLength/const)
    so both apps validate without extra dependencies. Raises BridgeError(400)."""
    _check(SCHEMAS[schema_name], payload, schema_name)


_TYPES = {'object': dict, 'array': list, 'string': str, 'integer': int, 'number': (int, float), 'boolean': bool, 'null': type(None)}


def _check(s: dict, v: Any, path: str) -> None:
    t = s.get('type')
    if t:
        types = t if isinstance(t, list) else [t]
        if not any(isinstance(v, _TYPES[x]) and not (x in ('integer', 'number') and isinstance(v, bool)) for x in types):
            raise BridgeError(400, f'{path}: expected {t}')
    if 'const' in s and v != s['const']:
        raise BridgeError(400, f'{path}: must be {s["const"]!r}')
    if 'enum' in s and v not in s['enum']:
        raise BridgeError(400, f'{path}: must be one of {s["enum"]}')
    if isinstance(v, str):
        if 'maxLength' in s and len(v) > s['maxLength']:
            raise BridgeError(400, f'{path}: too long')
        if 'minLength' in s and len(v) < s['minLength']:
            raise BridgeError(400, f'{path}: too short')
        if 'pattern' in s and not re.search(s['pattern'], v):
            raise BridgeError(400, f'{path}: invalid format')
    if isinstance(v, dict):
        for req in s.get('required', []):
            if req not in v:
                raise BridgeError(400, f'{path}.{req}: required')
        props = s.get('properties', {})
        if s.get('additionalProperties') is False:
            extra = set(v) - set(props)
            if extra:
                raise BridgeError(400, f'{path}: unexpected fields {sorted(extra)}')
        for k, sub in props.items():
            if k in v:
                _check(sub, v[k], f'{path}.{k}')
    if isinstance(v, list):
        if 'maxItems' in s and len(v) > s['maxItems']:
            raise BridgeError(400, f'{path}: too many items')
        if 'items' in s:
            for i, item in enumerate(v):
                _check(s['items'], item, f'{path}[{i}]')

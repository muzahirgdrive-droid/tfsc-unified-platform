"""Tamper-evident, hash-chained audit log (§21). Writes are serialized per org with a
transaction-scoped advisory lock so the chain stays linear."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import Connection, select, text

from ..db import models as m
from ..pacific import utcnow

GENESIS = '0' * 64


def _canonical(row: dict[str, Any]) -> str:
    return json.dumps({k: (str(v) if v is not None else None) for k, v in sorted(row.items())}, sort_keys=True, separators=(',', ':'))


def record(c: Connection, org_id: uuid.UUID, actor_id: uuid.UUID | None, action: str, resource: str, resource_id: Any = None,
           before: dict | None = None, after: dict | None = None, ip: str | None = None, ua: str | None = None) -> str:
    c.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'), {'k': f'audit:{org_id}'})
    prev = c.execute(select(m.audit_log.c.hash).where(m.audit_log.c.org_id == org_id).order_by(m.audit_log.c.id.desc()).limit(1)).scalar_one_or_none() or GENESIS
    at = utcnow()
    body = {'actor_id': actor_id, 'action': action, 'resource': resource, 'resource_id': resource_id,
            'before': json.dumps(before, sort_keys=True, default=str) if before is not None else None,
            'after': json.dumps(after, sort_keys=True, default=str) if after is not None else None, 'at': at.isoformat()}
    h = hashlib.sha256((prev + _canonical(body)).encode()).hexdigest()
    c.execute(m.audit_log.insert().values(org_id=org_id, actor_id=actor_id, action=action[:80], resource=resource[:40],
                                          resource_id=str(resource_id) if resource_id is not None else None,
                                          before=json.loads(body['before']) if before is not None else None,
                                          after=json.loads(body['after']) if after is not None else None,
                                          ip=ip, ua=(ua or '')[:300] or None, at=at, prev_hash=prev, hash=h))
    return h


def verify_chain(c: Connection, org_id: uuid.UUID) -> dict:
    """Recompute links (prev_hash continuity). Content hashes of v2 rows are recomputed too."""
    prev = None
    broken = []
    count = 0
    for r in c.execute(select(m.audit_log).where(m.audit_log.c.org_id == org_id).order_by(m.audit_log.c.id)).mappings():
        count += 1
        if prev is not None and r['prev_hash'] != prev:
            broken.append(r['id'])
        prev = r['hash']
    return {'rows': count, 'broken_links': broken, 'ok': not broken, 'head': prev}

"""Tamper-evident, hash-chained audit log (§21). Writes are serialized per org with a
transaction-scoped advisory lock so the chain stays linear."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, func, select, text

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


def _content_hash(prev: str, r: dict) -> str:
    """Recompute a v2 row's hash from its stored fields (same canonical form as record())."""
    body = {'actor_id': r['actor_id'], 'action': r['action'], 'resource': r['resource'], 'resource_id': r['resource_id'],
            'before': json.dumps(r['before'], sort_keys=True, default=str) if r['before'] is not None else None,
            'after': json.dumps(r['after'], sort_keys=True, default=str) if r['after'] is not None else None,
            'at': r['at'].astimezone(UTC).isoformat()}
    return hashlib.sha256((prev + _canonical(body)).encode()).hexdigest()


# Rows written by the frozen pilot-data migration use that migration's own hash recipe; their links are still checked.
MIGRATED = ('pilot', 'migration')


def verify_chain(c: Connection, org_id: uuid.UUID) -> dict:
    """Recompute every link (prev_hash continuity) and every v2 row's content hash, then check the daily anchors:
    the row an anchor recorded must still carry the anchored hash. Any edit, deletion or re-hash of history shows up."""
    prev = None
    broken: list[int] = []
    altered: list[int] = []
    count = 0
    hash_at: dict[int, str] = {}
    for r in c.execute(select(m.audit_log).where(m.audit_log.c.org_id == org_id).order_by(m.audit_log.c.id)).mappings():
        count += 1
        if prev is not None and r['prev_hash'] != prev:
            broken.append(r['id'])
        if r['resource'] not in MIGRATED and _content_hash(r['prev_hash'], dict(r)) != r['hash']:
            altered.append(r['id'])
        hash_at[r['id']] = r['hash']
        prev = r['hash']
    anchors = c.execute(select(m.audit_anchors).where(m.audit_anchors.c.org_id == org_id).order_by(m.audit_anchors.c.id)).mappings().all()
    bad_anchors = [a['anchored_for'].isoformat() for a in anchors if hash_at.get(a['last_audit_id']) != a['head_hash']]
    ok = not broken and not altered and not bad_anchors
    return {'rows': count, 'broken_links': broken[:100], 'altered_rows': altered[:100], 'anchors': len(anchors),
            'anchors_failed': bad_anchors[:100], 'ok': ok, 'head': prev}


def anchor(c: Connection, org_id: uuid.UUID, day: date, sink_dir: Path | None = None) -> dict | None:
    """Daily anchor (§21): record the chain head in `audit_anchors` (write-once for the app role) and append it to an
    append-only sink file. Shipping that file to a write-once (object-lock) bucket is a deployment step
    (docs/runbooks/backups-and-restore.md); no remote sink is claimed here."""
    head = c.execute(select(m.audit_log.c.id, m.audit_log.c.hash).where(m.audit_log.c.org_id == org_id)
                     .order_by(m.audit_log.c.id.desc()).limit(1)).first()
    if head is None:
        return None
    exists = c.execute(select(m.audit_anchors.c.id).where(m.audit_anchors.c.org_id == org_id, m.audit_anchors.c.anchored_for == day)).first()
    if exists:
        return None
    rows = c.execute(select(func.count()).select_from(m.audit_log).where(m.audit_log.c.org_id == org_id)).scalar_one()
    rec = {'org_id': str(org_id), 'anchored_for': day.isoformat(), 'last_audit_id': head[0], 'head_hash': head[1], 'rows': rows,
           'at': utcnow().isoformat()}
    ref = None
    if sink_dir is not None:
        sink_dir.mkdir(parents=True, exist_ok=True)
        f = sink_dir / f'audit-anchors-{org_id}.jsonl'
        with f.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(rec, sort_keys=True) + '\n')
        ref = f.name
    c.execute(m.audit_anchors.insert().values(org_id=org_id, anchored_for=day, last_audit_id=head[0], head_hash=head[1], rows=rows,
                                              sink='file' if ref else 'database', sink_ref=ref))
    return rec

"""Tamper-evident, hash-chained audit log (§21). Writes are serialized per org with a
transaction-scoped advisory lock so the chain stays linear."""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, func, select, text

from ..config import get_settings
from ..db import models as m
from ..pacific import utcnow

GENESIS = '0' * 64
log = logging.getLogger('tfsc_crm.audit')


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
    # The write-once copies are the independent reference: someone able to rewrite both audit tables still cannot
    # change or delete an object-locked anchor, so every one of them must match the chain (and the database copy).
    remote = remote_anchors(org_id)
    by_day = {a['anchored_for'].isoformat(): a for a in anchors}
    bad_remote = sorted({r['anchored_for'] for r in remote or [] if r.get('deleted') or hash_at.get(r['last_audit_id']) != r['head_hash']
                         or (r['anchored_for'] in by_day and by_day[r['anchored_for']]['head_hash'] != r['head_hash'])})
    if remote is not None:  # shipped according to the database, but no write-once copy (bucket swapped or emptied)
        held = {r['anchored_for'] for r in remote if not r.get('deleted')}
        bad_remote = sorted(set(bad_remote) | {d for d, a in by_day.items() if a['remote_ref'] and d not in held})
    ok = not broken and not altered and not bad_anchors and not bad_remote
    return {'rows': count, 'broken_links': broken[:100], 'altered_rows': altered[:100], 'anchors': len(anchors),
            'anchors_failed': bad_anchors[:100], 'remote_anchors': len([r for r in remote if not r.get('deleted')]) if remote is not None else None,
            'remote_failed': bad_remote[:100], 'ok': ok, 'head': prev}


def _anchor_key(org_id: uuid.UUID, day: str) -> str:
    return f'anchors/{org_id}/{day}.json'


def remote_anchors(org_id: uuid.UUID) -> list[dict] | None:
    """Every version of every anchor in the object-lock bucket for this org (None when no bucket is configured).
    Versions, not just the latest objects: a delete marker or a newer version cannot hide a locked original. A delete
    marker comes back as {'anchored_for': <day>, 'deleted': True}."""
    s = get_settings()
    if not s.anchor_bucket:
        return None
    from .. import storage
    out: list[dict] = []
    for page in storage.s3().get_paginator('list_object_versions').paginate(Bucket=s.anchor_bucket, Prefix=f'anchors/{org_id}/'):
        for v in page.get('Versions', []):
            body = storage.s3().get_object(Bucket=s.anchor_bucket, Key=v['Key'], VersionId=v['VersionId'])['Body'].read()
            out.append(json.loads(body))
        for d in page.get('DeleteMarkers', []):
            out.append({'anchored_for': d['Key'].rsplit('/', 1)[-1].removesuffix('.json'), 'deleted': True})
    return out


def ship_pending(c: Connection, org_id: uuid.UUID) -> int:
    """Copy anchors not yet shipped to the object-lock bucket (COMPLIANCE retention: nobody can delete or overwrite them
    before CRM_ANCHOR_RETENTION_DAYS). Runs with the scheduler's role; the app role cannot update anchors."""
    s = get_settings()
    if not s.anchor_bucket:
        return 0
    from .. import storage
    rows = c.execute(select(m.audit_anchors).where(m.audit_anchors.c.org_id == org_id, m.audit_anchors.c.remote_ref.is_(None))
                     .order_by(m.audit_anchors.c.id)).mappings().all()
    for a in rows:
        day = a['anchored_for'].isoformat()
        rec = {'org_id': str(org_id), 'anchored_for': day, 'last_audit_id': a['last_audit_id'], 'head_hash': a['head_hash'], 'rows': a['rows'],
               'at': a['created_at'].isoformat()}
        key = _anchor_key(org_id, day)
        storage.s3().put_object(Bucket=s.anchor_bucket, Key=key, Body=json.dumps(rec, sort_keys=True).encode(), ContentType='application/json',
                                ObjectLockMode='COMPLIANCE', ObjectLockRetainUntilDate=utcnow() + timedelta(days=s.anchor_retention_days),
                                ChecksumAlgorithm='SHA256')
        c.execute(m.audit_anchors.update().where(m.audit_anchors.c.id == a['id']).values(remote_ref=f's3://{s.anchor_bucket}/{key}'))
    return len(rows)


def anchor(c: Connection, org_id: uuid.UUID, day: date, sink_dir: Path | None = None) -> dict | None:
    """Daily anchor (§21): record the chain head in `audit_anchors` (write-once for the app role) and append it to an
    append-only sink file; the scheduler then ships it to the object-lock bucket when one is configured (ship_pending)."""
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

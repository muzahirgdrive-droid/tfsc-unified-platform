"""File storage (§4.2): profile photos, uploaded import files, legacy workbooks and export ZIPs.

Backends (CRM_STORAGE):
- `db` (default): the bytes live in `stored_files`; fine for a single-server pilot.
- `s3`: an S3-compatible bucket (MinIO self-hosted, or a cloud provider). `stored_files` keeps the metadata (type, size,
  SHA-256) and every read is checked against that hash, so a changed object is refused. Downloads use short-lived
  presigned URLs (CRM_S3_URL_TTL seconds, default 120) signed for the endpoint the browser can reach
  (CRM_S3_PUBLIC_ENDPOINT); the API checks permissions before it hands one out.
Uploads are virus-scanned before they reach this module (security/antivirus.py). Both backends can be read at the
same time, so switching to s3 keeps older database-held files readable.
"""
from __future__ import annotations

import hashlib
import uuid
from functools import lru_cache
from typing import Any

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert

from .config import get_settings
from .db import models as m


class StorageError(Exception):
    pass


def backend() -> str:
    return get_settings().storage


@lru_cache(maxsize=4)
def _client(endpoint: str) -> Any:
    import boto3
    from botocore.config import Config
    s = get_settings()
    return boto3.client('s3', endpoint_url=endpoint, region_name=s.s3_region, aws_access_key_id=s.s3_access_key,
                        aws_secret_access_key=s.s3_secret_key,
                        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}, retries={'max_attempts': 3}))


def s3() -> Any:
    return _client(get_settings().s3_endpoint)


def put(c: Connection, org_id: uuid.UUID, key: str, data: bytes, content_type: str, *, replace: bool = True) -> str:
    """Store `data` under `key`; returns its SHA-256. With replace=False an existing key is kept (content-addressed keys)."""
    sha = hashlib.sha256(data).hexdigest()
    if not replace and c.execute(select(m.stored_files.c.key).where(m.stored_files.c.key == key)).first():
        return sha
    s = get_settings()
    if s.storage == 's3':
        s3().put_object(Bucket=s.s3_bucket, Key=key, Body=data, ContentType=content_type, ChecksumAlgorithm='SHA256')
    row = {'org_id': org_id, 'content_type': content_type, 'size': len(data), 'sha256': sha,
           'data': data if s.storage == 'db' else None, 'backend': s.storage}
    c.execute(insert(m.stored_files).values(key=key, **row).on_conflict_do_update(index_elements=['key'], set_=row))
    return sha


def _row(c: Connection, key: str) -> dict:
    row = c.execute(select(m.stored_files).where(m.stored_files.c.key == key)).mappings().first()
    if not row:
        raise StorageError(f'No stored file {key}.')
    return dict(row)


def get(c: Connection, key: str) -> bytes:
    row = _row(c, key)
    if row['data'] is not None:
        return bytes(row['data'])
    body = s3().get_object(Bucket=get_settings().s3_bucket, Key=key)['Body'].read()
    if hashlib.sha256(body).hexdigest() != row['sha256']:
        raise StorageError(f'Stored file {key} does not match its recorded SHA-256.')
    return body


def download_url(c: Connection, key: str, filename: str) -> str | None:
    """A short-lived presigned URL for an object held in the bucket; None when the bytes are in the database."""
    row = _row(c, key)
    if row['data'] is not None:
        return None
    s = get_settings()
    url: str = _client(s.s3_public_endpoint or s.s3_endpoint).generate_presigned_url(
        'get_object', ExpiresIn=s.s3_url_ttl,
        Params={'Bucket': s.s3_bucket, 'Key': key, 'ResponseContentType': row['content_type'],
                'ResponseContentDisposition': f'attachment; filename="{filename}"'})
    return url


def delete(c: Connection, key: str) -> None:
    row = c.execute(select(m.stored_files.c.backend).where(m.stored_files.c.key == key)).first()
    if row and row[0] == 's3':
        s3().delete_object(Bucket=get_settings().s3_bucket, Key=key)
    c.execute(m.stored_files.delete().where(m.stored_files.c.key == key))


def init_buckets(attempts: int = 30) -> list[str]:
    """Create the files bucket and the object-lock anchor bucket if missing, and check the anchor bucket really has
    object lock (it cannot be added later). `python -m tfsc_crm.storage init` (compose/Helm one-shot job)."""
    import time

    from botocore.exceptions import ClientError, EndpointConnectionError
    s = get_settings()
    for i in range(attempts):  # the S3 server may still be starting
        try:
            existing = {b['Name'] for b in s3().list_buckets().get('Buckets', [])}
            break
        except EndpointConnectionError:
            if i == attempts - 1:
                raise
            time.sleep(2)
    made = []
    if s.storage == 's3' and s.s3_bucket not in existing:
        s3().create_bucket(Bucket=s.s3_bucket)
        made.append(s.s3_bucket)
    if s.anchor_bucket:
        if s.anchor_bucket not in existing:
            s3().create_bucket(Bucket=s.anchor_bucket, ObjectLockEnabledForBucket=True)
            made.append(s.anchor_bucket)
        try:
            enabled = s3().get_object_lock_configuration(Bucket=s.anchor_bucket)['ObjectLockConfiguration']['ObjectLockEnabled']
        except ClientError:
            enabled = None
        if enabled != 'Enabled':
            raise StorageError(f'Bucket {s.anchor_bucket} has no object lock; create a new bucket with object lock enabled.')
    return made


if __name__ == '__main__':  # pragma: no cover - deployment helper
    import sys
    if sys.argv[1:] == ['init']:
        print('created:', ', '.join(init_buckets()) or 'nothing (buckets exist)')
    else:
        raise SystemExit('usage: python -m tfsc_crm.storage init')

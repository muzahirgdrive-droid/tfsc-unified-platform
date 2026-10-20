"""Object storage, presigned downloads, upload virus scan and write-once audit anchors (§4.2, §21).

S3 cases run against a real S3-compatible server when CRM_TEST_S3_ENDPOINT is set (CI starts RustFS; locally e.g.
`docker run -p 19000:9000 -e RUSTFS_ACCESS_KEY=... -e RUSTFS_SECRET_KEY=... rustfs/rustfs:1.0.1`), and are skipped
otherwise. The virus-scan cases use a tiny clamd stand-in speaking the INSTREAM protocol.
"""
import hashlib
import io
import os
import socket
import struct
import threading
import time
import uuid

import httpx
import pytest
from sqlalchemy import text

from .conftest import new_phone

EICAR = b'X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'
S3 = os.getenv('CRM_TEST_S3_ENDPOINT')
needs_s3 = pytest.mark.skipif(not S3, reason='CRM_TEST_S3_ENDPOINT not set (no S3-compatible server)')


def _sys():
    from tfsc_crm.db.session import system_tx
    return system_tx()


def _reset():
    from tfsc_crm import storage
    from tfsc_crm.config import get_settings
    get_settings.cache_clear()
    storage._client.cache_clear()


@pytest.fixture
def s3(monkeypatch):
    import boto3
    from botocore.config import Config
    key, secret = os.environ['CRM_TEST_S3_ACCESS_KEY'], os.environ['CRM_TEST_S3_SECRET_KEY']
    client = boto3.client('s3', endpoint_url=S3, aws_access_key_id=key, aws_secret_access_key=secret, region_name='us-east-1',
                          config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}))
    suffix = uuid.uuid4().hex[:10]
    files, anchors = f'crm-files-{suffix}', f'crm-anchors-{suffix}'
    client.create_bucket(Bucket=files)
    client.create_bucket(Bucket=anchors, ObjectLockEnabledForBucket=True)
    for k, v in {'CRM_STORAGE': 's3', 'CRM_S3_ENDPOINT': S3, 'CRM_S3_ACCESS_KEY': key, 'CRM_S3_SECRET_KEY': secret, 'CRM_S3_BUCKET': files,
                 'CRM_ANCHOR_BUCKET': anchors, 'CRM_ANCHOR_RETENTION_DAYS': '1', 'CRM_S3_URL_TTL': '60'}.items():
        monkeypatch.setenv(k, v)
    _reset()
    yield {'client': client, 'files': files, 'anchors': anchors}
    monkeypatch.undo()
    _reset()


def _png() -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (64, 48), (10, 120, 200)).save(out, format='PNG')
    return out.getvalue()


@needs_s3
def test_files_live_in_the_bucket_and_reads_are_integrity_checked(ws, s3):
    a = ws['a']
    assert a.post(f"/api/users/{a.id}/photo", files={'file': ('me.png', io.BytesIO(_png()), 'image/png')}).status_code == 200
    with _sys() as c:
        row = c.execute(text("SELECT backend, data IS NULL, sha256, size FROM stored_files WHERE key = :k"), {'k': f'photos/{a.id}.jpg'}).one()
    assert row[0] == 's3' and row[1] is True  # metadata only in PostgreSQL
    obj = s3['client'].get_object(Bucket=s3['files'], Key=f'photos/{a.id}.jpg')['Body'].read()
    assert hashlib.sha256(obj).hexdigest() == row[2] and len(obj) == row[3]
    got = ws['b'].get(f"/api/users/{a.id}/photo")
    assert got.status_code == 200 and got.content == obj
    # an object changed behind the CRM's back is refused, not served
    s3['client'].put_object(Bucket=s3['files'], Key=f'photos/{a.id}.jpg', Body=b'not the photo')
    assert ws['b'].get(f"/api/users/{a.id}/photo").status_code == 404
    # anonymous access to the bucket is refused
    assert httpx.get(f"{S3}/{s3['files']}/photos/{a.id}.jpg").status_code == 403


@needs_s3
def test_export_download_is_a_short_lived_presigned_url_after_the_checks(ws, s3, monkeypatch):
    from tfsc_crm.pacific import business_date
    ws['b'].post('/api/activities', json={'name': 'Bucket Clinic', 'phone': new_phone(), 'called': True})
    today = business_date().isoformat()
    eid = ws['manager'].post('/api/exports', json={'kind': 'activities', 'start': today, 'end': today, 'reason': 'Bucket check'}).json()['id']
    assert ws['director'].post(f'/api/exports/{eid}/decision', json={'approve': True}).json()['status'] == 'ready'
    with _sys() as c:
        sha = c.execute(text('SELECT file_sha256 FROM exports WHERE id = :e'), {'e': eid}).scalar_one()
    assert ws['sup'].http.get(f'/api/exports/{eid}/download', follow_redirects=False).status_code in (403, 404)
    r = ws['manager'].http.get(f'/api/exports/{eid}/download', follow_redirects=False)
    assert r.status_code == 303 and r.headers['cache-control'] == 'no-store', r.text
    url = r.headers['location']
    assert url.startswith(S3) and 'X-Amz-Expires=60' in url
    body = httpx.get(url)
    assert body.status_code == 200 and hashlib.sha256(body.content).hexdigest() == sha
    assert 'attachment; filename="tfsc-crm-activities-' in body.headers['content-disposition']
    assert httpx.get(url.replace('X-Amz-Signature=', 'X-Amz-Signature=0')).status_code in (400, 403)  # tampered link
    monkeypatch.setenv('CRM_S3_URL_TTL', '1')
    _reset()
    short = ws['manager'].http.get(f'/api/exports/{eid}/download', follow_redirects=False).headers['location']
    time.sleep(2.5)
    assert httpx.get(short).status_code == 403  # expired


@needs_s3
def test_anchors_ship_to_the_object_lock_bucket_and_verification_uses_them(ws, s3):
    from botocore.exceptions import ClientError

    from tfsc_crm.pacific import utcnow
    from tfsc_crm.workers import scheduler
    with _sys() as c:
        org = c.execute(text("SELECT org_id FROM users WHERE email = 'super@crm.test'")).scalar_one()
        # a fresh day so this test owns the anchor
        c.execute(text("DELETE FROM audit_anchors WHERE anchored_for >= current_date - 1"))
    out = scheduler.job_audit_anchor(utcnow())
    assert out['anchored'] >= 1 and out['shipped'] >= 1, out
    with _sys() as c:
        day, ref = c.execute(text("SELECT anchored_for, remote_ref FROM audit_anchors WHERE org_id = :o ORDER BY id DESC LIMIT 1"), {'o': org}).one()
    key = f'anchors/{org}/{day.isoformat()}.json'
    assert ref == f"s3://{s3['anchors']}/{key}"
    head = s3['client'].head_object(Bucket=s3['anchors'], Key=key)
    assert head['ObjectLockMode'] == 'COMPLIANCE'
    v = ws['super'].post('/api/security/audit/verify').json()
    assert v['ok'] and v['remote_anchors'] >= 1 and v['remote_failed'] == [], v

    # the locked original cannot be removed, shortened or replaced in place ...
    version = head['VersionId']
    with pytest.raises(ClientError):
        s3['client'].delete_object(Bucket=s3['anchors'], Key=key, VersionId=version)
    # ... and a forged newer version or a delete marker is detected, not trusted
    s3['client'].put_object(Bucket=s3['anchors'], Key=key, Body=b'{"anchored_for": "%s", "head_hash": "%s", "last_audit_id": 1}' % (
        day.isoformat().encode(), b'f' * 64))
    v = ws['super'].post('/api/security/audit/verify').json()
    assert not v['ok'] and day.isoformat() in v['remote_failed'], v
    s3['client'].delete_object(Bucket=s3['anchors'], Key=key)  # plain delete: only adds a delete marker
    v = ws['super'].post('/api/security/audit/verify').json()
    assert not v['ok'] and day.isoformat() in v['remote_failed']


# ------------------------------------------------------------------ virus scan
class FakeClamd:
    """Speaks clamd's INSTREAM: FOUND for the EICAR test string, OK otherwise."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.scanned = 0
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                assert conn.recv(10) == b'zINSTREAM\0'
                data = b''
                while True:
                    size = struct.unpack('>I', self._exact(conn, 4))[0]
                    if size == 0:
                        break
                    data += self._exact(conn, size)
                self.scanned += 1
                conn.sendall(b'stream: Win.Test.EICAR_HDB-1 FOUND\0' if EICAR in data else b'stream: OK\0')

    @staticmethod
    def _exact(conn, n):
        buf = b''
        while len(buf) < n:
            buf += conn.recv(n - len(buf))
        return buf


@pytest.fixture
def clamd(monkeypatch):
    fake = FakeClamd()
    monkeypatch.setenv('CRM_CLAMAV', f'127.0.0.1:{fake.port}')
    _reset()
    yield fake
    fake.sock.close()
    monkeypatch.undo()
    _reset()


def _csv(name):
    return f'Provider Name,Phone,Call Attempt\n{name},{new_phone()},Yes\n'.encode()


def test_uploads_are_scanned_and_infected_files_refused_and_audited(ws, clamd):
    b = ws['b']
    ok = b.post('/api/imports', files={'file': ('clean.csv', io.BytesIO(_csv('Clean Clinic')), 'text/csv')})
    assert ok.status_code == 201 and clamd.scanned == 1, ok.text
    bad = _csv('Bad Clinic') + EICAR + b'\n'
    r = b.post('/api/imports', files={'file': ('bad.csv', io.BytesIO(bad), 'text/csv')})
    assert r.status_code == 422 and 'virus scanner' in r.text
    with _sys() as c:
        assert c.execute(text("SELECT count(*) FROM stored_files WHERE sha256 = :s"), {'s': hashlib.sha256(bad).hexdigest()}).scalar_one() == 0
        rej = c.execute(text("SELECT after FROM audit_log WHERE action = 'file.rejected' ORDER BY id DESC LIMIT 1")).scalar_one()
    assert rej['filename'] == 'bad.csv' and 'EICAR' in rej['signature']
    # photos and legacy workbooks go through the same scan
    r = b.post(f"/api/users/{b.id}/photo", files={'file': ('x.png', io.BytesIO(EICAR), 'image/png')})
    assert r.status_code == 422
    r = ws['super'].post('/api/admin/legacy-imports', files={'file': ('w.xlsx', io.BytesIO(b'PK' + EICAR), 'application/octet-stream')})
    assert r.status_code == 422


def test_scanner_unreachable_means_no_upload(ws, monkeypatch):
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens there
    monkeypatch.setenv('CRM_CLAMAV', f'127.0.0.1:{port}')
    _reset()
    try:
        data = _csv('Unscanned Clinic')
        r = ws['b'].post('/api/imports', files={'file': ('u.csv', io.BytesIO(data), 'text/csv')})
        assert r.status_code == 503 and 'virus scanner' in r.text
        with _sys() as c:
            assert c.execute(text("SELECT count(*) FROM stored_files WHERE sha256 = :s"), {'s': hashlib.sha256(data).hexdigest()}).scalar_one() == 0
    finally:
        monkeypatch.undo()
        _reset()


def test_hosted_mode_requires_an_explicit_scanner_choice(monkeypatch):
    from tfsc_crm.config import Settings
    monkeypatch.setenv('CRM_MODE', 'hosted')
    monkeypatch.setenv('CRM_ORIGIN', 'https://crm.example.com')
    monkeypatch.setenv('CRM_PHONE_KEY_SECRET', 'x' * 32)
    monkeypatch.setenv('CRM_FIELD_KEY', 'eA==')
    monkeypatch.delenv('CRM_CLAMAV', raising=False)
    with pytest.raises(RuntimeError, match='CRM_CLAMAV'):
        Settings().validate()
    monkeypatch.setenv('CRM_CLAMAV', 'off')
    Settings().validate()
    monkeypatch.setenv('CRM_STORAGE', 's3')
    with pytest.raises(RuntimeError, match='CRM_S3_ENDPOINT'):
        Settings().validate()

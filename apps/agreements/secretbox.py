"""Encryption at rest for mailbox credentials (AES-256-GCM).

The key comes from TFSC_SECRET_KEY (the installer generates it into .env). The local single-computer edition keeps a
generated key file in its data folder instead. Each value is bound to its purpose (associated data), so a ciphertext
copied to another company's row or field does not decrypt.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import database as db


class SecretKeyMissing(RuntimeError):
    pass


def _key() -> bytes:
    raw = os.getenv('TFSC_SECRET_KEY', '').strip()
    if not raw and db.LOCAL:
        path = db.DATA / 'secret.key'
        if not path.exists():
            db.DATA.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as f:
                    f.write(secrets.token_urlsafe(32))
            except FileExistsError:
                pass
        raw = path.read_text().strip()
    if len(raw) < 32:
        raise SecretKeyMissing('TFSC_SECRET_KEY is not set on the server, so mailbox passwords cannot be stored. '
                               'Run "sudo ./tfsc.sh update" (it adds the key) or set a random value of at least 32 characters.')
    return hashlib.sha256(b'tfsc-secretbox-v1:' + raw.encode()).digest()


def available() -> bool:
    try:
        _key()
        return True
    except SecretKeyMissing:
        return False


def seal(value: str, purpose: str) -> str:
    nonce = secrets.token_bytes(12)
    sealed = AESGCM(_key()).encrypt(nonce, value.encode(), purpose.encode())
    return 'v1:' + base64.b64encode(nonce + sealed).decode()


def open_(value: str | None, purpose: str) -> str:
    if not value:
        return ''
    if not value.startswith('v1:'):
        raise ValueError('Unknown secret format.')
    raw = base64.b64decode(value[3:])
    try:
        return AESGCM(_key()).decrypt(raw[:12], raw[12:], purpose.encode()).decode()
    except InvalidTag:
        raise ValueError('A stored mailbox password could not be decrypted (the server key changed). Enter it again.') from None

"""Blind indexes and field encryption (ADR-0006).

phone_key = HMAC-SHA256(CRM_PHONE_KEY_SECRET, E.164) so a database dump does not
reveal the set of numbers. Field encryption is AES-256-GCM with CRM_FIELD_KEY;
values are stored as 'v1:' + base64(nonce|ciphertext). Without a key (local/test
mode) values are stored as 'p1:' + plaintext so the format is explicit.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

_DEV_SECRET = 'local-development-phone-key-not-for-production'  # noqa: S105 - dev only; hosted mode requires CRM_PHONE_KEY_SECRET


def _phone_secret() -> bytes:
    return (get_settings().phone_key_secret or _DEV_SECRET).encode()


def blind_index(value: str | None, kind: str = 'phone') -> str | None:
    if not value:
        return None
    return hmac.new(_phone_secret(), f'{kind}:{value}'.encode(), hashlib.sha256).hexdigest()


def phone_key(e164: str | None) -> str | None:
    return blind_index(e164, 'phone')


def email_key(email: str | None) -> str | None:
    return blind_index(email.lower() if email else None, 'email')


def _aes() -> AESGCM | None:
    key = get_settings().field_key
    return AESGCM(base64.b64decode(key)) if key else None


def encrypt(plain: str | None) -> str | None:
    if plain is None or plain == '':
        return plain
    aes = _aes()
    if aes is None:
        return 'p1:' + plain
    nonce = os.urandom(12)
    return 'v1:' + base64.b64encode(nonce + aes.encrypt(nonce, plain.encode(), None)).decode()


def decrypt(stored: str | None) -> str | None:
    if not stored:
        return stored
    if stored.startswith('p1:'):
        return stored[3:]
    if stored.startswith('v1:'):
        aes = _aes()
        if aes is None:
            raise RuntimeError('CRM_FIELD_KEY is required to read encrypted fields.')
        raw = base64.b64decode(stored[3:])
        return aes.decrypt(raw[:12], raw[12:], None).decode()
    return stored  # legacy plaintext

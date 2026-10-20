"""First-run setup: a one-time setup key on disk authorizes creating the first Super Admin."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from sqlalchemy import select

from ..db import models as m
from ..db.session import system_tx


def data_dir() -> Path:
    p = Path(os.getenv('CRM_DATA_DIR', str(Path(__file__).resolve().parents[2] / '.data')))
    p.mkdir(parents=True, exist_ok=True)
    return p


def setup_required() -> bool:
    with system_tx() as c:
        return c.execute(select(m.users.c.id).limit(1)).first() is None


def ensure_setup_key() -> None:
    try:
        if not setup_required():
            return
    except Exception:  # database not migrated yet; health endpoint will report it
        return
    key = data_dir() / 'setup-key.txt'
    if not key.exists():
        try:
            with key.open('x', encoding='utf-8') as f:
                f.write(secrets.token_urlsafe(24))
            key.chmod(0o600)
        except FileExistsError:
            pass


def read_setup_key() -> str | None:
    key = data_dir() / 'setup-key.txt'
    return key.read_text(encoding='utf-8').strip() if key.exists() else None


def consume_setup_key() -> None:
    (data_dir() / 'setup-key.txt').unlink(missing_ok=True)

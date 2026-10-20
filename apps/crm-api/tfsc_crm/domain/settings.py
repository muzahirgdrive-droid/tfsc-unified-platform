"""Org settings and effective-dated targets."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import Connection, select

from ..db import models as m

DEFAULTS: dict[str, Any] = {
    'lock_windows': {'manual_minutes': 60, 'import_minutes': 15, 'import_min': 10, 'import_max': 20},
    'celebration': {'template': '🎉 Congratulations to {agent} for a successful sale! Thank you, {supervisor} and management.', 'seconds': 6},
    'names': {'suffix_comma': True},
    'dialer': {'mode': 'tel', 'template': None},
    'lead_protection_days': 90,
    'auto_assignment': {'round_robin': False},
    'agent_meeting_label': 'With management',
    'anti_scraping': {'prospects_per_hour': 500},
}


def get(c: Connection, org_id: uuid.UUID, key: str) -> Any:
    """Per-transaction memo: the first read loads every setting of the org in one query."""
    cache = c.info.setdefault('settings_cache', {})
    if org_id not in cache:
        cache[org_id] = dict(c.execute(select(m.settings.c.key, m.settings.c.value).where(m.settings.c.org_id == org_id)).all())
    v = cache[org_id].get(key)
    return DEFAULTS.get(key) if v is None else v


def ensure_partition(c: Connection, parent: str, d: date) -> None:
    done = c.info.setdefault('partitions_ensured', set())
    k = (parent, d.year, d.month)
    if k not in done:
        from sqlalchemy import text
        c.execute(text('SELECT crm_ensure_partition_for(:p, :d)'), {'p': parent, 'd': d})
        done.add(k)


def target_for(c: Connection, org_id: uuid.UUID, d: date) -> dict:
    t = m.targets
    r = c.execute(select(t).where(t.c.org_id == org_id, t.c.effective_date <= d).order_by(t.c.effective_date.desc()).limit(1)).mappings().first()
    if not r:
        return {'call_target': 170, 'conn_target': 0.6, 'bands': {'extraordinary': 0.8, 'satisfactory': 0.65, 'average': 0.5, 'below_average': 0.35}}
    return {'call_target': int(r['call_target']), 'conn_target': float(r['conn_target']), 'bands': r['bands'], 'effective_date': r['effective_date']}

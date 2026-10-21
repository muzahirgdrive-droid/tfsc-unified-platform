"""Database backup status (§4.2, §23).

On the compose stack the `backup` service (infra/compose/postgres/tfsc-backup) records every pgBackRest backup and
every automated point-in-time restore test in ops.backup_runs; WAL archiving shows in pg_stat_archiver. A managed
database that takes its own backups has neither, and then there is nothing to report (None).
Shown in the Security Center and exported as crm_backup_* / crm_restore_test_* / crm_wal_archive_* metrics.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text

from .db.session import system_tx

KINDS = ('backup', 'restore_test')
log = logging.getLogger(__name__)


def _run(row: Any, last_success_at: datetime | None) -> dict[str, Any]:
    d = row['detail'] or {}
    seconds = d.get('seconds')
    if row['kind'] == 'restore_test' and 'restore_seconds' in d:  # time to a usable server: restore + WAL replay
        seconds = d.get('restore_seconds', 0) + d.get('recovery_seconds', 0)
    return {'ok': row['ok'], 'at': row['finished_at'], 'last_success_at': last_success_at, 'type': d.get('type'),
            'database_bytes': d.get('database_bytes'), 'target': d.get('target'), 'seconds': seconds, 'failure': d.get('failure')}


def status() -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    try:
        with system_tx() as c:
            mode, archived, failed = c.execute(text(
                "SELECT current_setting('archive_mode'), last_archived_time, last_failed_time FROM pg_stat_archiver")).one()
            if mode in ('on', 'always'):
                out['wal'] = {'last_archived_at': archived, 'failing': bool(failed and (archived is None or failed > archived))}
            if c.execute(text("SELECT to_regclass('ops.backup_runs') IS NOT NULL")).scalar_one():
                for kind in KINDS:
                    latest = c.execute(text('SELECT kind, ok, finished_at, detail FROM ops.backup_runs WHERE kind = :k '
                                            'ORDER BY finished_at DESC LIMIT 1'), {'k': kind}).mappings().first()
                    if latest:
                        ok_at = c.execute(text('SELECT max(finished_at) FROM ops.backup_runs WHERE kind = :k AND ok'), {'k': kind}).scalar_one()
                        out[kind] = _run(latest, ok_at)
    except Exception as e:  # no database, or a login role that may not read ops (managed database)
        log.debug('backup status unavailable: %s', type(e).__name__)
    return out or None


def gauges() -> dict[str, float]:
    g: dict[str, float] = {}
    s = status() or {}
    if 'wal' in s:
        at = s['wal']['last_archived_at']
        g['crm_wal_archive_last_success_timestamp_seconds'] = at.timestamp() if at else 0.0
        g['crm_wal_archive_failing'] = float(s['wal']['failing'])
    for kind in KINDS:
        if kind in s:
            at = s[kind]['last_success_at']
            g[f'crm_{kind}_last_success_timestamp_seconds'] = at.timestamp() if at else 0.0
            g[f'crm_{kind}_last_ok'] = float(s[kind]['ok'])
    return g

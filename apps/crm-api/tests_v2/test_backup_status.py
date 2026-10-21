"""Backup status (§4.2, §23): what the compose `backup` service records in ops.backup_runs reaches the Security Center
and the metrics; a database without it (managed backups) reports nothing rather than a false "no backup"."""
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

SCRIPT = Path(__file__).resolve().parents[3] / 'infra/compose/postgres/tfsc-backup'


def _ddl() -> str:
    """The table definition exactly as the backup service creates it."""
    return re.search(r"<<'SQL'\n(CREATE SCHEMA IF NOT EXISTS ops;.*?)\nSQL", SCRIPT.read_text(), re.S).group(1)


def test_backup_status_reaches_security_center_and_metrics(ws):
    from tfsc_crm import backups, observability
    from tfsc_crm.db.session import system_tx
    assert backups.status() is None and backups.gauges() == {}  # test database: no archiving, no backup service
    now = datetime.now(UTC)
    with system_tx() as c:
        c.execute(text(_ddl()))
        ins = text('INSERT INTO ops.backup_runs (kind, ok, started_at, finished_at, detail) VALUES (:k, :ok, :at, :at, CAST(:d AS jsonb))')
        c.execute(ins, {'k': 'backup', 'ok': True, 'at': now - timedelta(hours=25), 'd': '{"type": "full", "database_bytes": 57075059, "seconds": 3}'})
        c.execute(ins, {'k': 'backup', 'ok': False, 'at': now - timedelta(hours=1), 'd': '{"type": "diff", "output": "/backups/repo ..."}'})
        c.execute(ins, {'k': 'restore_test', 'ok': True, 'at': now - timedelta(minutes=50),
                        'd': '{"target": "2026-10-21 04:21:48+00", "restore_seconds": 2, "recovery_seconds": 3, "check_seconds": 2}'})
        c.execute(ins, {'k': 'pitr_marker', 'ok': True, 'at': now, 'd': '{"run": "x", "marker": "before"}'})
    try:
        s = backups.status()
        assert s['backup']['ok'] is False and s['backup']['type'] == 'diff'  # the latest run failed ...
        assert abs((s['backup']['last_success_at'] - (now - timedelta(hours=25))).total_seconds()) < 1  # ... the last good one is older
        assert s['restore_test']['ok'] is True and s['restore_test']['seconds'] == 5 and s['restore_test']['target'].startswith('2026-10-21')
        g = observability.gauges()
        assert g['crm_backup_last_ok'] == 0.0 and g['crm_restore_test_last_ok'] == 1.0
        assert abs(g['crm_backup_last_success_timestamp_seconds'] - (now - timedelta(hours=25)).timestamp()) < 1
        assert 'crm_wal_archive_failing' not in g  # archive_mode is off on the test server
        assert 'crm_restore_test_last_success_timestamp_seconds' in observability.render(g)

        r = ws['director'].get('/api/security/overview')
        assert r.status_code == 200
        b = r.json()['backups']
        assert b['backup']['ok'] is False and b['restore_test']['ok'] is True and b['wal'] is None
        assert 'output' not in b['backup'] and '/backups/repo' not in r.text  # tool output stays on the server
        assert ws['manager'].get('/api/security/overview').status_code == 403
    finally:
        with system_tx() as c:
            c.execute(text('DROP SCHEMA ops CASCADE'))
    assert ws['director'].get('/api/security/overview').json()['backups'] is None

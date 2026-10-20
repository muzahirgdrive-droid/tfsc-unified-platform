"""§25.1-2: baseline matches pilot, every migration downgrades, pilot data is copied losslessly."""
import json
import os
import subprocess
import sys
import tempfile

from sqlalchemy import create_engine, text

from .conftest import APP, alembic, create_database, drop_database


def _counts(url, tables):
    eng = create_engine(url)
    with eng.connect() as c:
        out = {t: c.execute(text(f'SELECT count(*) FROM {t}')).scalar_one() for t in tables}
    eng.dispose()
    return out


def test_up_down_up_on_empty_database():
    url = create_database('crm_mig')
    try:
        alembic(url, 'upgrade', 'head')
        alembic(url, 'downgrade', 'base')
        assert _counts(url, ['alembic_version']) == {'alembic_version': 0}
        alembic(url, 'upgrade', 'head')
        c = _counts(url, ['domains', 'projects', 'orgs', 'targets'])
        assert c == {'domains': 9, 'projects': 28, 'orgs': 1, 'targets': 1}
    finally:
        drop_database(url)


def test_pilot_data_migrates_and_downgrade_restores():
    url = create_database('crm_pilot')
    try:
        alembic(url, 'upgrade', '0001_pilot_baseline')
        env = {**os.environ, 'CRM_DATABASE_URL': url, 'CRM_DATA_DIR': tempfile.mkdtemp(prefix='pilot-'), 'CRM_MODE': 'local', 'CRM_ORIGIN': 'http://testserver'}
        r = subprocess.run([sys.executable, 'tests_v2/pilot_seed_script.py'], cwd=APP, env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-3000:]
        assert json.loads(r.stdout.strip().splitlines()[-1])['lead_stage'] == 'qualified_lead'
        pilot = ['crm_users', 'crm_activities', 'crm_leads', 'crm_audit', 'crm_conflicts']
        before = _counts(url, pilot)
        alembic(url, 'upgrade', 'head')
        v2 = _counts(url, ['users', 'activities', 'followups', 'leads', 'lead_ownership_claims', 'flags', 'phone_registry'])
        assert v2 == {'users': 5, 'activities': 5, 'followups': 1, 'leads': 1, 'lead_ownership_claims': 1, 'flags': 1, 'phone_registry': 4}
        eng = create_engine(url)
        with eng.connect() as c:
            states = dict(c.execute(text('SELECT state, count(*) FROM phone_registry GROUP BY state')).all())
            assert states == {'none': 2, 'followup': 1, 'lead': 1}
            # research row keeps no lock and no commit timestamp; raw original is kept
            row = c.execute(text("SELECT first_committed_at, edit_deadline, raw, phone_display, phone_key FROM activities WHERE provider_name='Research Only Clinic'")).one()
            assert row.first_committed_at is None and row.edit_deadline is None and row.raw['_pilot']['phone'] == '(212) 555-0101'
            assert row.phone_display.startswith('p1:') and len(row.phone_key) == 64 and '555' not in row.phone_key
            # audit chain is linked
            chain = c.execute(text('SELECT prev_hash, hash FROM audit_log ORDER BY id')).all()
            assert chain[0].prev_hash == '0' * 64 and all(chain[i].prev_hash == chain[i - 1].hash for i in range(1, len(chain)))
            assert c.execute(text("SELECT count(*) FROM pg_class WHERE relname LIKE 'activities_20%'")).scalar_one() >= 5
        eng.dispose()
        assert _counts(url, pilot) == before  # pilot tables untouched
        alembic(url, 'downgrade', '0002_v2_foundation')
        assert _counts(url, ['users', 'activities']) == {'users': 0, 'activities': 0}
        assert _counts(url, pilot) == before
        alembic(url, 'upgrade', 'head')
        assert _counts(url, ['users', 'activities']) == {'users': 5, 'activities': 5}
    finally:
        drop_database(url)

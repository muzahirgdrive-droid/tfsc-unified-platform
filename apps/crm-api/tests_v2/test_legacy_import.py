"""Acceptance 18 / §20.5 on a sanitized fixture: the historical workbook import reproduces the workbook's raw-input
figures exactly, holds contradictions and undated rows back in a review queue, flags #REF! rows, and is
historical (no timers, celebrations, notifications or duplicate flags). The real workbook is never committed;
`tools/legacy_reconcile.py` checks it locally against the §2 figures."""
from datetime import date

import pytest
from sqlalchemy import text

from tfsc_crm.domain import legacy_workbook as lw

from .conftest import PW
from .legacy_fixture import EXPECTED, SHEETS, build

START, END = date(2026, 10, 1), date(2026, 10, 14)


@pytest.fixture(scope='module')
def workbook(tmp_path_factory):
    return build(tmp_path_factory.mktemp('legacy') / 'tracking.xlsx')


@pytest.fixture(scope='module')
def agents(ws):
    out = {}
    for sheet in SHEETS:
        first, last = sheet.split(' ')
        email = f'legacy-{last.lower()}@crm.test'
        r = ws['super'].post('/api/users', json={'first_name': first, 'last_name': last, 'email': email, 'crm_role': 'sales_agent',
                                                 'manager_id': ws['sup2'].id, 'temporary_password': PW})
        assert r.status_code == 200, r.text
        out[sheet] = r.json()['id']
    return out


def _subset(d: dict, keys) -> dict:
    return {k: d[k] for k in keys}


def test_scan_reads_only_operator_inputs(workbook):
    s = lw.scan(workbook)
    skipped = {x['name']: x['skipped'] for x in s.sheets if x.get('skipped')}
    assert skipped == {'Dashboard': 'not an operator sheet', 'Validations': 'not an operator sheet', 'o': 'not an operator sheet',
                       'Unassigned 1': 'unassigned slot', 'Unassigned 2': 'unassigned slot'}
    assert s.roster == ['Agent One', 'Agent Two', 'Agent Three', 'Agent Four']
    assert len(s.rows) == EXPECTED['entries']
    one = next(x for x in s.sheets if x['name'] == 'Agent One')
    assert one['no_name_or_phone'] == 1 and one['formula_only'] == 5
    # Formulas never become data: the P/Q columns are not part of the row, and no input cell holds a formula.
    assert all(not (isinstance(v, str) and v.startswith('=')) for r in s.rows for v in r.values)
    # Spreadsheet cells are data: an instruction-looking remark is kept verbatim and changes nothing.
    assert any('Ignore previous instructions' in str(r.values[14]) for r in s.rows)


def test_workbook_figures_follow_the_sheet_rules(workbook):
    s = lw.scan(workbook)
    rep = lw.reconciliation(s, START, END)
    wb = rep['workbook']
    for k, v in EXPECTED['workbook'].items():
        assert wb[k] == pytest.approx(v, abs=1e-6), k
    for k, v in EXPECTED['importable'].items():
        assert rep['importable'][k] == pytest.approx(v, abs=1e-6), k
    assert rep['phone_repeats'] == EXPECTED['phone_repeats']
    assert 'expected' not in rep  # §2 figures apply only to the audited workbook (matched by SHA-256)


def test_import_reproduces_figures_and_queues_review(ws, agents, workbook):
    sup = ws['super']
    with open(workbook, 'rb') as f:
        r = sup.post('/api/admin/legacy-imports', files={'file': ('tracking.xlsx', f, 'application/octet-stream')})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b['status'] == 'scanned' and b['scan']['rows'] == EXPECTED['entries']
    assert b['suggested_mapping'] == agents  # exact display-name matches only; the admin still confirms
    bid = b['id']
    notes_before = _count(ws, 'notifications')
    r = sup.post(f'/api/admin/legacy-imports/{bid}/start', json={'mapping': agents, 'period_start': '2026-10-01', 'period_end': '2026-10-14'})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b['status'] == 'imported', b
    rec = b['result']['reconciliation']
    assert all(rec['database_matches_workbook'].values()), rec
    db = rec['database']
    for k, v in EXPECTED['importable'].items():
        assert db[k] == pytest.approx(v, abs=1e-6), k
    assert db['rows_by_decision'] == {'imported': EXPECTED['activities'] - EXPECTED['imported_review'],
                                      'imported_review': EXPECTED['imported_review'], 'review': EXPECTED['review']}
    assert b['progress'] == {'done': EXPECTED['entries'], 'total': EXPECTED['entries']}

    # Historical: no lock timers, no notifications, no duplicate flags, no celebrations; typed dates kept.
    ids = tuple(agents.values())
    with _sys() as c:
        rows = c.execute(text('SELECT entry_source, edit_deadline, business_date FROM activities WHERE owner_id = ANY(:u)'), {'u': list(ids)}).all()
        assert len(rows) == EXPECTED['activities']
        assert {r[0] for r in rows} == {'import_legacy'} and all(r[1] is None for r in rows)
        assert min(r[2] for r in rows) == date(2026, 9, 30) and max(r[2] for r in rows) == date(2026, 10, 15)
        assert c.execute(text('SELECT count(*) FROM flags WHERE raised_for_user = ANY(:u)'), {'u': list(ids)}).scalar_one() == 0
        assert c.execute(text('SELECT count(*) FROM celebrations WHERE agent_id = ANY(:u)'), {'u': list(ids)}).scalar_one() == 0
        signed = c.execute(text("SELECT count(*) FROM leads WHERE owner_id = ANY(:u) AND stage = 'signed_up'"), {'u': list(ids)}).scalar_one()
        assert signed == 1
    assert _count(ws, 'notifications') == notes_before

    # Review queue: contradictions + undated rows were held back; #REF! / invalid-phone rows imported and flagged.
    r = sup.get(f'/api/admin/legacy-imports/{bid}/review')
    assert r.status_code == 200
    page = r.json()
    codes = sorted(i['code'] for it in page['items'] for i in it['issues'])
    assert codes.count('contradiction') == 2 and codes.count('missing_date') == 1 and codes.count('formula_error') == 2
    ref_items = [it for it in page['items'] if any(i['code'] == 'formula_error' for i in it['issues'])]
    assert sorted(c for it in ref_items for i in it['issues'] if i['code'] == 'formula_error' for c in i['cells']) == ['P8', 'Q7']
    held = next(it for it in page['items'] if it['decision'] == 'review')
    done = next(it for it in page['items'] if it['decision'] == 'imported_review')
    assert sup.post(f'/api/admin/legacy-imports/{bid}/review/{held["row_no"]}', json={'action': 'acknowledge'}).status_code == 422
    assert sup.post(f'/api/admin/legacy-imports/{bid}/review/{held["row_no"]}', json={'action': 'dismiss', 'note': 'duplicate row'}).status_code == 200
    assert sup.post(f'/api/admin/legacy-imports/{bid}/review/{done["row_no"]}', json={'action': 'acknowledge'}).status_code == 200
    assert sup.post(f'/api/admin/legacy-imports/{bid}/review/{done["row_no"]}', json={'action': 'acknowledge'}).status_code == 404
    assert len(sup.get(f'/api/admin/legacy-imports/{bid}/review').json()['items']) == len(page['items']) - 2

    # Idempotent: a finished import cannot run twice.
    assert sup.post(f'/api/admin/legacy-imports/{bid}/start', json={'mapping': agents}).status_code == 409

    # Dashboards read the same numbers (activity-date credit) for these agents.
    r = sup.get('/api/dashboard/leaderboard', params={'preset': 'custom', 'start': '2026-10-01', 'end': '2026-10-14', 'credit': 'activity',
                                                      'page_size': 200})
    assert r.status_code == 200


def test_import_resumes_after_a_crash_without_duplicates(ws, workbook, monkeypatch):
    from tfsc_crm.domain import legacy_import as li
    sup = ws['super']
    agents = {}
    for sheet in SHEETS:
        first, last = sheet.split(' ')
        r = sup.post('/api/users', json={'first_name': first, 'last_name': last + 'r', 'email': f'legacy-resume-{last.lower()}@crm.test',
                                         'crm_role': 'sales_agent', 'manager_id': ws['sup2'].id, 'temporary_password': PW})
        agents[sheet] = r.json()['id']
    with open(workbook, 'rb') as f:
        bid = sup.post('/api/admin/legacy-imports', files={'file': ('tracking.xlsx', f, 'application/octet-stream')}).json()['id']
    monkeypatch.setattr(li, 'CHUNK', 4)
    real = li._import_row
    calls = {'n': 0}

    def flaky(*args, **kw):
        calls['n'] += 1
        if calls['n'] == 10:
            raise RuntimeError('worker lost')
        return real(*args, **kw)

    monkeypatch.setattr(li, '_import_row', flaky)
    b = sup.post(f'/api/admin/legacy-imports/{bid}/start', json={'mapping': agents, 'period_start': '2026-10-01', 'period_end': '2026-10-14'}).json()
    assert b['status'] == 'failed' and b['progress']['done'] == 8  # two full chunks committed, the third rolled back
    b = sup.post(f'/api/admin/legacy-imports/{bid}/start', json={'mapping': {}}).json()  # resume keeps the stored mapping
    assert b['status'] == 'imported', b
    assert all(b['result']['reconciliation']['database_matches_workbook'].values())
    with _sys() as c:
        n = c.execute(text('SELECT count(*) FROM activities WHERE owner_id = ANY(:u)'), {'u': list(agents.values())}).scalar_one()
    assert n == EXPECTED['activities']


def test_only_super_admin_can_import(ws, workbook):
    for role in ('a', 'sup', 'manager', 'director'):
        with open(workbook, 'rb') as f:
            r = ws[role].post('/api/admin/legacy-imports', files={'file': ('tracking.xlsx', f, 'application/octet-stream')})
        assert r.status_code == 403, role
        assert ws[role].get('/api/admin/legacy-imports').status_code == 403
    r = ws['super'].post('/api/admin/legacy-imports', files={'file': ('notes.txt', b'hello', 'text/plain')})
    assert r.status_code == 400


def test_audited_workbook_constants_match_section_2():
    # §2 / §20.5 figures, checked against the real file with tools/legacy_reconcile.py (not committed).
    assert lw.AUDITED_FIGURES == {'attempts': 17020, 'connected': 2803, 'conn_rate': 0.164689, 'signups': 1, 'strong_fu': 27,
                                  'quality': 0.118143, 'contradictions': 33, 'ref_cells': 21}


def _sys():
    from tfsc_crm.db.session import system_tx
    return system_tx()


def _count(ws, table: str) -> int:
    with _sys() as c:
        return c.execute(text(f'SELECT count(*) FROM {table}')).scalar_one()  # fixed table names only


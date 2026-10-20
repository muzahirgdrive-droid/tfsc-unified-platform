"""P1/P2 capture rules through the HTTP API (acceptance 2-5, 14, 15)."""
from datetime import timedelta

from sqlalchemy import text

from .conftest import new_phone


def _db(ws):
    from tfsc_crm.db.session import system_tx
    return system_tx()


def test_health_and_time(ws):
    r = ws['a'].get('/api/health')
    assert r.status_code == 200 and r.json()['database'] == '0003_seed_and_pilot_data'
    t = ws['a'].get('/api/time').json()
    zones = {i['zone']: i for i in t['items']}
    assert zones['America/Los_Angeles']['is_business_clock'] and zones['Asia/Karachi']['diff_vs_pakistan_hours'] == 0
    assert zones['America/Phoenix']['abbreviation'] == 'MST'


def test_data_hunting_row_has_no_lock_and_no_points(ws):
    r = ws['a'].post('/api/activities', json={'name': 'research clinic llc', 'phone': new_phone()})
    assert r.status_code == 200, r.text
    act = r.json()['activity']
    assert act['provider_name'] == 'Research Clinic, LLC'
    assert act['state'] == 'data_hunting' and act['edit_deadline'] is None and act['points'] == 0
    assert 'domain_id' not in act and 'project_id' not in act  # agents never see routing


def test_called_no_starts_timer_without_attempt_and_deadline_never_moves(ws):
    a = ws['a']
    act = a.post('/api/activities', json={'name': 'No Answer Clinic', 'phone': new_phone(), 'called': False}).json()['activity']
    assert act['state'] == 'committed' and act['points'] == 0 and act['stage'] == 'No call'
    deadline = act['edit_deadline']
    act2 = a.patch(f"/api/activities/{act['id']}", json={'version': act['version'], 'called': True}).json()['activity']
    assert act2['points'] == 10 and act2['edit_deadline'] == deadline
    act3 = a.patch(f"/api/activities/{act['id']}", json={'version': act2['version'], 'called': None}).json()['activity']
    assert act3['edit_deadline'] == deadline  # un-setting never resets the timer


def test_connected_requires_called(ws):
    r = ws['a'].post('/api/activities', json={'name': 'Bad Row', 'phone': new_phone(), 'connected': True})
    assert r.status_code == 400 and 'Connected requires Called' in r.json()['error']


def test_lock_rules_manual_61_minutes(ws):
    a, sup, gl = ws['b'], ws['sup'], ws['a']
    act = a.post('/api/activities', json={'name': 'Lock Clinic', 'phone': new_phone(), 'called': True}).json()['activity']
    with _db(ws) as c:
        c.execute(text('UPDATE activities SET edit_deadline = now() - interval \'1 minute\', first_committed_at = now() - interval \'61 minutes\' WHERE id = :i'),
                  {'i': act['id']})
    assert a.patch(f"/api/activities/{act['id']}", json={'version': act['version'], 'remarks': 'late'}).status_code == 403
    assert gl.patch(f"/api/activities/{act['id']}", json={'version': act['version'], 'remarks': 'leader'}).status_code in (403, 404)
    assert sup.patch(f"/api/activities/{act['id']}", json={'version': act['version'], 'remarks': 'no reason'}).status_code == 400
    r = sup.patch(f"/api/activities/{act['id']}", json={'version': act['version'], 'remarks': 'fixed', 'reason': 'Verified with agent'})
    assert r.status_code == 200, r.text
    revs = sup.get(f"/api/activities/{act['id']}/revisions").json()
    assert revs[-1]['reason'] == 'Verified with agent'
    assert ws['sup2'].get(f"/api/activities/{act['id']}").status_code == 404  # out of scope


def test_import_lock_window_15_minutes(ws):
    a = ws['a']
    r = a.post('/api/activities/batch', json={'request_key': 'lock-window-test', 'rows': [{'name': 'Paste Clinic', 'phone': new_phone(), 'called': 'Yes'}]})
    assert r.status_code == 200, r.text
    aid = r.json()['rows'][0]['activity_id']
    act = a.get(f'/api/activities/{aid}').json()
    from datetime import datetime
    window = datetime.fromisoformat(act['edit_deadline']) - datetime.fromisoformat(act['first_committed_at'])
    assert window == timedelta(minutes=15)
    with _db(ws) as c:
        c.execute(text("UPDATE activities SET edit_deadline = first_committed_at + interval '15 minutes' - interval '16 minutes' WHERE id = :i"), {'i': aid})
    act = a.get(f'/api/activities/{aid}').json()
    assert act['state'] == 'locked' and not act['can_edit']


def test_refine_and_save_200_rows_keeps_3_red(ws):
    rows = [{'name': f'dr john doe{i} md', 'phone': f'212-555-{2000 + i:04d}', 'email': f'John{i}@Gmial.com', 'called': 'Yes',
             'connected': 'Yes' if i % 2 else 'No'} for i in range(197)]
    rows += [{'name': '', 'phone': '212-555-3001'}, {'name': 'Bad Phone', 'phone': '123'}, {'name': 'Contradiction', 'phone': '212-555-3003', 'connected': 'Yes'}]
    a = ws['b']
    pre = a.post('/api/refine', json={'rows': rows}).json()
    assert pre['counts']['red'] == 3 and pre['rows'][0]['refined']['name'] == 'John Doe0, M.D.'
    assert pre['rows'][0]['refined']['email_suggestion'] == 'john0@gmail.com'
    r = a.post('/api/activities/batch', json={'request_key': 'accept-200-rows', 'rows': rows})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out['saved'] == 197 and out['rejected'] == 3
    assert [x['row_no'] for x in out['rows'] if x['status'] == 'rejected'] == [197, 198, 199]
    again = a.post('/api/activities/batch', json={'request_key': 'accept-200-rows', 'rows': rows}).json()
    assert again['replayed'] and again['saved'] == 197
    assert a.post('/api/activities/batch', json={'request_key': 'accept-200-rows', 'rows': rows[:5]}).status_code == 409


def test_new_user_default_domain_and_project_stamp(ws):
    from tfsc_crm.db.session import system_tx
    a = ws['a']
    act = a.post('/api/activities', json={'name': 'Stamp Clinic', 'phone': new_phone(), 'called': True}).json()['activity']
    with system_tx() as c:
        row = c.execute(text('SELECT domain_id, project_id FROM activities WHERE id = :i'), {'i': act['id']}).one()
        b_dom = c.execute(text("SELECT default_domain_id FROM users WHERE email = 'b@crm.test'")).scalar_one()
    assert row.domain_id == 'scribe_align' and row.project_id == 'P0007' and b_dom == 'scribe_align'
    sup_view = ws['manager'].get(f"/api/activities/{act['id']}").json()
    assert sup_view['project_id'] == 'P0007'


def test_pacific_business_date_for_pkt_late_night():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from tfsc_crm.pacific import business_date
    pkt = datetime(2026, 10, 20, 1, 30, tzinfo=ZoneInfo('Asia/Karachi'))  # Tuesday 01:30 PKT
    assert business_date(pkt).isoformat() == '2026-10-19'  # Monday in PT

"""P3 integrity: duplicates, strong follow-up, leads, concurrency (acceptance 6-10)."""
import concurrent.futures as cf

from sqlalchemy import text

from .conftest import new_phone


def _flags(ws, who='sup'):
    return ws[who].get('/api/flags?status=all').json()['items']


def _make_lead(ws, phone, agent='a'):
    a, sup = ws[agent], ws['sup']
    act = a.post('/api/activities', json={'name': 'Lead Practice LLC', 'phone': phone, 'called': True, 'connected': True}).json()['activity']
    s = a.post(f"/api/activities/{act['id']}/strong", json={'target_window': '2-3 weeks', 'note': 'Demo interest'})
    assert s.status_code == 200, s.text
    sid = s.json()['id']
    strong = next(x for x in sup.get('/api/strong').json() if x['id'] == sid)
    r = sup.post(f'/api/strong/{sid}/decide', json={'version': strong['version'], 'decision': 'confirmed'})
    assert r.status_code == 200, r.text
    r = sup.post(f'/api/strong/{sid}/convert', json={})
    assert r.status_code == 200, r.text
    return act, sid, r.json()


def test_amber_duplicate_followup_neutral_notice(ws):
    phone = new_phone()
    a, b = ws['a'], ws['b']
    r = a.post('/api/activities', json={'name': 'Follow Clinic', 'phone': phone, 'called': True, 'followup_needed': True, 'next_followup': '2026-12-01'})
    assert r.status_code == 200
    r = ws['c'].post('/api/activities', json={'name': 'Follow Clinic', 'phone': phone, 'called': True})
    body = r.json()
    assert body['duplicate_notice'].startswith('Already in follow-up') and body['duplicate_severity'] == 'notice'
    assert 'Atest' not in r.text and ws['a'].user['display_name'] not in r.text  # never reveals the other agent
    flag = next(f for f in _flags(ws, 'super') if f['kind'] == 'duplicate_followup' and f['raised_for_user'] == ws['c'].id)
    assert flag['severity'] == 'amber'
    # both supervisors and the super admin were notified
    for who in ('sup', 'sup2', 'super'):
        kinds = [n['kind'] for n in ws[who].get('/api/notifications').json()['items']]
        assert 'flag.amber' in kinds, who
    assert ws['c'].get('/api/flags').status_code == 403
    assert b is not None


def test_red_protected_lead_and_points_capped(ws):
    phone = new_phone()
    _make_lead(ws, phone)
    r = ws['c'].post('/api/activities', json={'name': 'Lead Practice LLC', 'phone': phone, 'called': True, 'connected': True, 'text_sent': True})
    body = r.json()
    assert body['duplicate_notice'].startswith('Protected lead')
    assert body['activity']['points'] == 10  # no points above attempt while in conflict
    assert body['activity']['conflict'] == 'review'
    red = [f for f in _flags(ws, 'sup2') if f['kind'] == 'duplicate_lead' and f['raised_for_user'] == ws['c'].id]
    assert red and red[0]['severity'] == 'red'
    # live blocking alert: one event per supervisor / manager / Super Admin in either reporting line, none to agents
    from sqlalchemy import text

    from tfsc_crm.db.session import system_tx
    with system_tx() as c:
        events = c.execute(text("SELECT payload FROM outbox WHERE topic = 'flag.raised' AND payload->>'flag_id' = :f"),
                           {'f': red[0]['id']}).scalars().all()
    assert {e['audience'] for e in events} == {f'user:{ws[k].id}' for k in ('sup', 'sup2', 'manager', 'super')}
    assert phone not in str(events) and 'Lead Practice' not in str(events)
    pre = ws['b'].get('/api/precheck', params={'phone': phone}).json()
    assert pre['state'] == 'protected' and 'A' not in pre['label'].split()  # no identity leak
    # resolve as data error -> credit restored
    f = red[0]
    r = ws['sup2'].post(f"/api/flags/{f['id']}/resolve", json={'version': f['version'], 'resolution': 'data_error', 'note': 'Wrong number typed'})
    assert r.status_code == 200, r.text
    act = ws['c'].get(f"/api/activities/{body['activity']['id']}").json()
    assert act['points'] == 30 and act['conflict'] is None


def test_strong_followup_flow_and_auto_owner_first_connector(ws):
    phone = new_phone()
    # b connects first, a requests strong FU later -> owner = b (first connector)
    ws['b'].post('/api/activities', json={'name': 'First Connect Clinic', 'phone': phone, 'called': True, 'connected': True})
    act = ws['a'].post('/api/activities', json={'name': 'First Connect Clinic', 'phone': phone, 'called': True, 'connected': True}).json()['activity']
    sid = ws['a'].post(f"/api/activities/{act['id']}/strong", json={}).json()['id']
    strong = next(x for x in ws['a'].get('/api/strong').json() if x['id'] == sid)
    # agents cannot confirm
    assert ws['a'].post(f'/api/strong/{sid}/decide', json={'version': strong['version'], 'decision': 'confirmed'}).status_code == 403
    assert ws['a'].post(f'/api/strong/{sid}/convert', json={}).status_code == 403
    ws['sup'].post(f'/api/strong/{sid}/decide', json={'version': strong['version'], 'decision': 'confirmed'})
    queue = {x['id']: x for x in ws['sup'].get('/api/strong?decision=confirmed').json()}
    assert queue[sid]['suggested_owner_id'] == ws['b'].id and queue[sid]['suggested_owner_rule'] == 'first_connector'
    lead = ws['sup'].post(f'/api/strong/{sid}/convert', json={}).json()
    assert lead['owner_id'] == ws['b'].id
    # strong confirmed credited to a's activity (50)
    assert ws['a'].get(f"/api/activities/{act['id']}").json()['points'] == 50


def test_concurrent_conversions_exactly_one_owner(ws):
    phone = new_phone()
    act = ws['a'].post('/api/activities', json={'name': 'Race Clinic', 'phone': phone, 'called': True, 'connected': True}).json()['activity']
    sid = ws['a'].post(f"/api/activities/{act['id']}/strong", json={}).json()['id']
    v = next(x for x in ws['sup'].get('/api/strong').json() if x['id'] == sid)['version']
    ws['sup'].post(f'/api/strong/{sid}/decide', json={'version': v, 'decision': 'confirmed'})
    with cf.ThreadPoolExecutor(16) as pool:  # §26: 100 parallel conversions -> exactly 1 owner
        codes = list(pool.map(lambda who: ws[who].post(f'/api/strong/{sid}/convert', json={}).status_code, ['sup', 'manager', 'director', 'super'] * 25))
    assert codes.count(200) == 1 and all(c == 409 for c in codes if c != 200), codes
    from tfsc_crm.db.session import system_tx
    with system_tx() as c:
        assert c.execute(text('SELECT count(*) FROM leads WHERE origin_activity_id = :a'), {'a': act['id']}).scalar_one() == 1


def test_agent_lead_view_has_no_meeting_or_routing_fields(ws):
    phone = new_phone()
    _act, _sid, lead = _make_lead(ws, phone)
    from tfsc_crm.db.session import system_tx
    with system_tx() as c:
        c.execute(text("UPDATE leads SET stage = 'meeting' WHERE id = :i"), {'i': lead['id']})
        org = c.execute(text('SELECT org_id FROM leads WHERE id = :i'), {'i': lead['id']}).scalar_one()
        c.execute(text("""INSERT INTO meetings (id, org_id, lead_id, organizer_id, title, starts_at, ends_at, meet_url, status)
                          VALUES (gen_random_uuid(), :o, :l, (SELECT id FROM users WHERE email='sup@crm.test'), 'Secret demo', now(), now(), 'p1:https://meet.google.com/abc', 'scheduled')"""),
                  {'o': org, 'l': lead['id']})
    r = ws['a'].get(f"/api/leads/{lead['id']}")
    assert r.status_code == 200
    body = r.json()
    for forbidden in ('meet', 'Secret demo', 'domain_id', 'project_id', 'starts_at', 'attendees', 'project_owner'):
        assert forbidden not in r.text, forbidden
    assert body['stage_label'] == 'With management'
    sup = ws['sup'].get(f"/api/leads/{lead['id']}").json()
    assert sup['meetings'][0]['title'] == 'Secret demo'


def test_signup_reverse_and_lost_rules(ws):
    phone = new_phone()
    act, _sid, lead = _make_lead(ws, phone)
    sup, a = ws['sup'], ws['a']
    detail = sup.get(f"/api/leads/{lead['id']}").json()
    assert a.post(f"/api/leads/{lead['id']}/transition", json={'version': detail['version'], 'action': 'sign_up'}).status_code == 403
    r = sup.post(f"/api/leads/{lead['id']}/transition", json={'version': detail['version'], 'action': 'sign_up'})
    assert r.status_code == 200, r.text
    assert a.get(f"/api/activities/{act['id']}").json()['points'] == 100
    v = r.json()['version']
    assert sup.post(f"/api/leads/{lead['id']}/transition", json={'version': v, 'action': 'reverse_sale'}).status_code == 400  # reason required
    r = sup.post(f"/api/leads/{lead['id']}/transition", json={'version': v, 'action': 'reverse_sale', 'reason': 'Client cancelled'})
    assert r.status_code == 200 and r.json()['stage'] == 'qualified_lead'
    assert a.get(f"/api/activities/{act['id']}").json()['points'] == 50
    from tfsc_crm.db.session import system_tx
    with system_tx() as c:
        total = c.execute(text('SELECT sum(delta_points) FROM score_ledger WHERE activity_id = :a'), {'a': act['id']}).scalar_one()
        assert total == 50  # ledger sum always equals ladder value
        reasons = [r[0] for r in c.execute(text('SELECT reason FROM score_ledger WHERE activity_id = :a ORDER BY at, delta_points DESC'), {'a': act['id']})]
        assert reasons[:4] == ['attempt', 'connect', 'strong', 'signup'] or 'reversal' in reasons

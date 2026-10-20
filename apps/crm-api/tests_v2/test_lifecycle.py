"""Lifecycle rules (§10-§13): follow-up actions, strong follow-up decisions, lead transitions and reassignment —
including every refusal (scope, stale version, missing reason, wrong stage, Agreement-free paths)."""
from sqlalchemy import text

from .conftest import new_phone
from .test_api_pipeline import _make_lead


def _followup(ws, who='a'):
    phone = new_phone()
    act = ws[who].post('/api/activities', json={'name': 'Lifecycle Clinic', 'phone': phone, 'called': True, 'followup_needed': True,
                                                'next_followup': '2026-12-03'}).json()['activity']
    items = ws[who].get('/api/followups', params={'status': 'open'}).json()['items']
    return phone, next(f for f in items if f['activity_id'] == act['id'])


def test_followup_actions_and_scope(ws):
    phone, f = _followup(ws)
    act = lambda who, body: ws[who].post(f"/api/followups/{f['id']}/action", json=body)  # noqa: E731
    assert act('a', {'version': f['version'], 'action': 'note'}).status_code == 400  # a note needs text
    assert act('a', {'version': f['version'], 'action': 'reschedule'}).status_code == 400  # and a date
    assert act('b', {'version': f['version'], 'action': 'note', 'note': 'peer'}).status_code == 404  # same team, not owner
    assert act('c', {'version': f['version'], 'action': 'note', 'note': 'other team'}).status_code == 404
    assert act('sup2', {'version': f['version'], 'action': 'note', 'note': 'other supervisor'}).status_code == 404
    assert act('a', {'version': f['version'], 'action': 'note', 'note': 'Left a voicemail'}).status_code == 200
    assert act('a', {'version': f['version'], 'action': 'note', 'note': 'stale'}).status_code == 409
    v = f['version'] + 1
    assert act('a', {'version': v, 'action': 'reschedule', 'due_at': '2026-12-10T17:00:00Z'}).status_code == 200
    assert ws['b'].get('/api/precheck', params={'phone': phone}).json()['state'] == 'followup'
    assert act('sup', {'version': v + 1, 'action': 'done', 'note': 'Booked a call'}).status_code == 200  # supervisor in scope
    done = next(x for x in ws['a'].get('/api/followups', params={'status': 'all'}).json()['items'] if x['id'] == f['id'])
    assert done['status'] == 'done' and done['outcome_note'] == 'Booked a call' and len(done['notes']) == 2
    # the last open follow-up closed: the number is no longer "in follow-up" for other agents
    assert ws['b'].get('/api/precheck', params={'phone': phone}).json()['state'] == 'seen'
    _, g = _followup(ws)
    assert ws['a'].post(f"/api/followups/{g['id']}/action", json={'version': g['version'], 'action': 'cancel'}).status_code == 200


def test_strong_followup_request_and_decision_rules(ws):
    a, sup = ws['a'], ws['sup']
    research = a.post('/api/activities', json={'name': 'Research Only', 'phone': new_phone()}).json()['activity']
    assert a.post(f"/api/activities/{research['id']}/strong", json={}).status_code == 409  # needs a committed call attempt
    act = a.post('/api/activities', json={'name': 'Strong Rules Clinic', 'phone': new_phone(), 'called': True, 'connected': True}).json()['activity']
    sid = a.post(f"/api/activities/{act['id']}/strong", json={'note': 'Wants pricing'}).json()['id']
    assert a.post(f"/api/activities/{act['id']}/strong", json={}).status_code == 409  # one open request per activity
    s = next(x for x in sup.get('/api/strong').json() if x['id'] == sid)
    assert a.post(f'/api/strong/{sid}/decide', json={'version': s['version'], 'decision': 'confirmed'}).status_code == 403  # agents cannot confirm
    assert ws['sup2'].post(f'/api/strong/{sid}/decide', json={'version': s['version'], 'decision': 'confirmed'}).status_code == 404  # out of scope
    assert sup.post(f'/api/strong/{sid}/decide', json={'version': s['version'] + 1, 'decision': 'confirmed'}).status_code == 409
    assert sup.post(f'/api/strong/{sid}/decide', json={'version': s['version'], 'decision': 'rejected'}).status_code == 400  # reason required
    assert sup.post(f'/api/strong/{sid}/decide', json={'version': s['version'], 'decision': 'rejected', 'reason': 'Not interested'}).status_code == 200
    assert sup.post(f'/api/strong/{sid}/decide', json={'version': s['version'] + 1, 'decision': 'confirmed'}).status_code == 409  # decided
    # a rejected request can be raised again later
    assert a.post(f"/api/activities/{act['id']}/strong", json={}).status_code == 200


def test_lead_transitions_follow_the_stage_rules(ws):
    sup, phone = ws['sup'], new_phone()
    _act, _sid, lead = _make_lead(ws, phone)
    lid = lead['id']
    cur = lambda: sup.get(f'/api/leads/{lid}').json()  # noqa: E731
    go = lambda body: sup.post(f'/api/leads/{lid}/transition', json=body)  # noqa: E731
    assert ws['a'].post(f'/api/leads/{lid}/transition', json={'version': cur()['version'], 'action': 'sign_up'}).status_code == 403
    assert ws['sup2'].post(f'/api/leads/{lid}/transition', json={'version': cur()['version'], 'action': 'sign_up'}).status_code == 404
    assert go({'version': cur()['version'], 'action': 'reverse_sale', 'reason': 'x'}).status_code == 409  # not signed
    assert go({'version': cur()['version'] + 5, 'action': 'lost', 'reason': 'x'}).status_code == 409  # stale
    assert go({'version': cur()['version'], 'action': 'lost'}).status_code == 400  # reason required
    assert go({'version': cur()['version'], 'action': 'lost', 'reason': 'Went with a competitor'}).status_code == 200
    assert ws['b'].get('/api/precheck', params={'phone': phone}).json()['state'] != 'protected'  # protection released
    assert go({'version': cur()['version'], 'action': 'reopen', 'reason': 'Came back'}).status_code == 200
    assert ws['b'].get('/api/precheck', params={'phone': phone}).json()['state'] == 'protected'
    assert go({'version': cur()['version'], 'action': 'sign_up'}).status_code == 200
    assert go({'version': cur()['version'], 'action': 'reverse_sale'}).status_code == 400  # reason required
    assert go({'version': cur()['version'], 'action': 'reverse_sale', 'reason': 'Client cancelled'}).status_code == 200
    assert cur()['stage'] == 'qualified_lead'
    # a lead with a scheduled meeting cannot be marked lost
    assert sup.post(f'/api/leads/{lid}/meetings', json={'title': 'Demo', 'starts_at': '2026-12-02T18:00:00Z'}).status_code == 200
    assert go({'version': cur()['version'], 'action': 'lost', 'reason': 'x'}).status_code == 409


def test_reassignment_rules_and_attribution(ws):
    sup = ws['sup']
    _act, _sid, lead = _make_lead(ws, new_phone())
    lid, v = lead['id'], sup.get(f"/api/leads/{lead['id']}").json()['version']
    body = {'version': v, 'to_user': ws['b'].id, 'reason': 'Coverage', 'incentive_split': {ws['a'].id: 0.5, ws['b'].id: 0.5}}
    assert sup.post(f'/api/leads/{lid}/reassign', json={**body, 'incentive_split': {}}).status_code == 400
    assert sup.post(f'/api/leads/{lid}/reassign', json={**body, 'incentive_split': {ws['b'].id: 0.6}}).status_code == 400  # not 100%
    assert sup.post(f'/api/leads/{lid}/reassign', json={**body, 'to_user': ws['c'].id}).status_code == 404  # c is outside sup's scope
    assert ws['a'].post(f'/api/leads/{lid}/reassign', json=body).status_code == 403
    assert sup.post(f'/api/leads/{lid}/reassign', json={**body, 'version': v + 3}).status_code == 409
    assert sup.post(f'/api/leads/{lid}/reassign', json=body).status_code == 200
    assert sup.get(f'/api/leads/{lid}').json()['owner_id'] == ws['b'].id
    from tfsc_crm.db.session import system_tx
    with system_tx() as c:
        shares = dict(c.execute(text("SELECT user_id::text, share FROM attribution_ledger WHERE lead_id = :l AND role = 'nurture'"), {'l': lid}).all())
        moved = c.execute(text('SELECT from_user::text, reason FROM lead_assignments WHERE lead_id = :l AND to_user = :b'),
                          {'l': lid, 'b': ws['b'].id}).all()
    assert {k: float(v) for k, v in shares.items()} == {ws['a'].id: 0.5, ws['b'].id: 0.5}
    assert [tuple(r) for r in moved] == [(ws['a'].id, 'Coverage')]  # audited move with its reason

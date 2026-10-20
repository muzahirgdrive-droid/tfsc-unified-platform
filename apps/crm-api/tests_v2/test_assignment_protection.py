"""§14.4 rule 3 (round-robin per team, weighted by open-lead capacity, off by default) and §27 lead protection
(90 days without activity -> supervisor prompted to release or renew; never released with a scheduled meeting)."""
from datetime import timedelta

import pytest
from sqlalchemy import text

from .conftest import PW, Client, new_phone
from .test_api_pipeline import _make_lead


def _agent(ws, key):
    r = ws['manager'].post('/api/users', json={'first_name': key.capitalize(), 'last_name': 'Test', 'email': f'{key}@crm.test',
                                                'crm_role': 'sales_agent', 'manager_id': ws['sup'].id, 'temporary_password': PW})
    assert r.status_code == 200, r.text
    c = Client(ws['app']).login(f'{key}@crm.test')
    assert c.post('/api/auth/password', json={'current_password': PW, 'new_password': PW + 'x'}).status_code == 200
    assert c.post('/api/auth/password', json={'current_password': PW + 'x', 'new_password': PW}).status_code == 200
    return Client(ws['app']).login(f'{key}@crm.test')


def _confirmed_strong(ws, agent) -> str:
    act = agent.post('/api/activities', json={'name': 'Rotation Dental', 'phone': new_phone(), 'called': True, 'connected': True}).json()['activity']
    sid = agent.post(f"/api/activities/{act['id']}/strong", json={'target_window': '2-3 weeks'}).json()['id']
    v = next(x for x in ws['sup'].get('/api/strong').json() if x['id'] == sid)['version']
    assert ws['sup'].post(f'/api/strong/{sid}/decide', json={'version': v, 'decision': 'confirmed'}).status_code == 200
    return sid


def _suggested(ws, sid):
    return next(x for x in ws['sup'].get('/api/strong?decision=confirmed').json() if x['id'] == sid)


@pytest.fixture(scope='module')
def rr(ws):
    r1, r2, r3 = (_agent(ws, k) for k in ('rr1', 'rr2', 'rr3'))
    team = ws['manager'].post('/api/teams', json={'name': 'Rotation Team', 'kind': 'team', 'supervisor_id': ws['sup'].id,
                                                   'member_ids': [r1.id, r2.id, r3.id]})
    assert team.status_code == 200, team.text
    assert team.json()['auto_assign'] is False and team.json()['lead_capacity'] == 25  # off by default
    return {'r1': r1, 'r2': r2, 'r3': r3, 'team': team.json()}


def _patch_team(ws, team_id, **vals):
    t = next(x for x in ws['manager'].get('/api/teams').json() if x['id'] == team_id)
    return ws['manager'].patch(f'/api/teams/{team_id}', json={'version': t['version'], **vals})


def test_round_robin_only_when_on_and_weighted_by_capacity(ws, rr):
    r1, r2, r3, team = rr['r1'], rr['r2'], rr['r3'], rr['team']
    sids = [_confirmed_strong(ws, r1) for _ in range(3)]
    # the requester leaves: rule 1 (first connector = requester) and rule 2 no longer apply
    off = ws['manager'].post(f'/api/users/{r1.id}/offboard', json={'reason': 'Left the company'})
    assert off.status_code == 200, off.text

    # round-robin is off by default: no automatic owner, the supervisor must choose
    assert _suggested(ws, sids[0])['suggested_owner_id'] is None
    r = ws['sup'].post(f'/api/strong/{sids[0]}/convert', json={})
    assert r.status_code == 409 and 'Choose an owner' in r.text

    # permissions: a Supervisor cannot switch team assignment policy, an agent even less; groups have no round-robin
    t = next(x for x in ws['manager'].get('/api/teams').json() if x['id'] == team['id'])
    assert ws['sup'].patch(f"/api/teams/{team['id']}", json={'version': t['version'], 'auto_assign': True}).status_code == 403
    assert ws['b'].patch(f"/api/teams/{team['id']}", json={'version': t['version'], 'auto_assign': True}).status_code == 403
    group = next(x for x in ws['sup'].get('/api/teams?kind=group').json())
    assert ws['sup'].patch(f"/api/teams/{group['id']}", json={'version': group['version'], 'auto_assign': True}).status_code == 400
    assert _patch_team(ws, team['id'], lead_capacity=0).status_code == 422

    r = _patch_team(ws, team['id'], auto_assign=True, lead_capacity=1)
    assert r.status_code == 200 and r.json()['auto_assign'] and r.json()['lead_capacity'] == 1, r.text
    s = _suggested(ws, sids[0])
    assert s['suggested_owner_rule'] == 'round_robin' and s['suggested_owner_id'] in (r2.id, r3.id)
    first = ws['sup'].post(f'/api/strong/{sids[0]}/convert', json={}).json()
    second = ws['sup'].post(f'/api/strong/{sids[1]}/convert', json={}).json()
    assert first['owner_rule'] == second['owner_rule'] == 'round_robin'
    assert {first['owner_id'], second['owner_id']} == {r2.id, r3.id}  # one each: the less loaded agent goes next
    # everyone at capacity: no automatic owner again; an explicit choice needs no override reason
    assert ws['sup'].post(f'/api/strong/{sids[2]}/convert', json={}).status_code == 409
    third = ws['sup'].post(f'/api/strong/{sids[2]}/convert', json={'owner_id': r2.id})
    assert third.status_code == 200 and third.json()['owner_rule'] == 'chosen', third.text

    from tfsc_crm.db.session import system_tx
    with system_tx() as c:
        rows = c.execute(text("SELECT reason FROM lead_assignments WHERE lead_id = CAST(:l AS uuid)"), {'l': first['id']}).scalars().all()
        assert rows == ['Automatic owner: round_robin']
        # the policy change is audited
        assert c.execute(text("SELECT count(*) FROM audit_log WHERE action = 'team.updated' AND resource_id = :t AND after->>'auto_assign' = 'True'"),
                         {'t': team['id']}).scalar_one() == 1


def test_offboarding_shares_open_leads_round_robin(ws, rr):
    r2, r3, team = rr['r2'], rr['r3'], rr['team']
    assert _patch_team(ws, team['id'], lead_capacity=10).status_code == 200
    held = [x for x in ws['sup'].get('/api/leads').json()['items'] if x['owner_id'] == r2.id]
    assert held
    off = ws['manager'].post(f'/api/users/{r2.id}/offboard', json={'reason': 'Moved on'})
    assert off.status_code == 200, off.text
    assert off.json()['leads_round_robin'] == len(held) == off.json()['leads_moved']
    owners = {x['id']: x['owner_id'] for x in ws['sup'].get('/api/leads').json()['items']}
    assert all(owners[x['id']] == r3.id for x in held)
    assert _patch_team(ws, team['id'], auto_assign=False).status_code == 200


def _backdate(lead_id, days=100):
    from tfsc_crm.db.session import system_tx
    with system_tx() as c:
        p = {'l': lead_id, 'd': timedelta(days=days)}
        c.execute(text('UPDATE leads SET created_at = now() - :d, stage_changed_at = now() - :d, '
                       'protection_renewed_at = CASE WHEN protection_renewed_at IS NULL THEN NULL ELSE now() - :d END WHERE id = :l'), p)
        c.execute(text('UPDATE activities SET updated_at = now() - :d WHERE phone_key = (SELECT phone_key FROM leads WHERE id = :l)'), p)
        c.execute(text('UPDATE lead_assignments SET at = now() - :d WHERE lead_id = :l'), p)
        c.execute(text('UPDATE meetings SET updated_at = now() - :d WHERE lead_id = :l'), p)


def _prompt():
    from tfsc_crm.pacific import utcnow
    from tfsc_crm.workers import scheduler
    return scheduler.job_lead_protection(utcnow())['prompted']


def _flags(ws, lead_id, status='open'):
    return [f for f in ws['sup'].get(f'/api/flags?kind=lead_protection_review&status={status}').json()['items'] if f['lead_id'] == lead_id]


def test_protection_review_renew_and_release(ws):
    _act, _sid, lead = _make_lead(ws, new_phone())
    lid = lead['id']
    fresh = ws['sup'].get(f'/api/leads/{lid}').json()['protection']
    assert fresh['window_days'] == 90 and fresh['days_inactive'] == 0 and not fresh['review_pending']
    assert _prompt() == 0  # nothing is inactive yet

    _backdate(lid)
    assert _prompt() == 1
    assert _prompt() == 0  # prompted once, not every day
    [flag] = _flags(ws, lid)
    assert flag['severity'] == 'amber' and flag['message'].startswith('No activity on this lead for 10')
    # answered on the lead (renew/release), never by closing the flag
    r = ws['sup'].post(f"/api/flags/{flag['id']}/resolve", json={'version': flag['version'], 'resolution': 'data_error', 'note': 'close it'})
    assert r.status_code == 409, r.text
    detail = ws['sup'].get(f'/api/leads/{lid}').json()
    assert detail['protection']['review_pending'] and detail['stage'] == 'qualified_lead'  # never released automatically
    from tfsc_crm.db.session import system_tx
    with system_tx() as c:
        assert c.execute(text("SELECT count(*) FROM notifications WHERE kind = 'lead.protection_review' AND payload->>'lead_id' = :l "
                              "AND user_id = (SELECT id FROM users WHERE email = 'sup@crm.test')"), {'l': lid}).scalar_one() == 1

    # the server decides who may answer: the owner agent cannot, an out-of-scope supervisor cannot even see it
    assert ws['a'].post(f'/api/leads/{lid}/protection', json={'action': 'renew', 'version': detail['version']}).status_code == 403
    assert ws['sup2'].post(f'/api/leads/{lid}/protection', json={'action': 'renew', 'version': detail['version']}).status_code == 404
    assert ws['sup'].post(f'/api/leads/{lid}/protection', json={'action': 'renew', 'version': detail['version'] - 1}).status_code == 409

    r = ws['sup'].post(f'/api/leads/{lid}/protection', json={'action': 'renew', 'reason': 'Decision maker back in May', 'version': detail['version']})
    assert r.status_code == 200, r.text
    assert not r.json()['protection']['review_pending'] and r.json()['protection']['days_inactive'] == 0
    assert not _flags(ws, lid) and _flags(ws, lid, 'resolved')[0]['resolution'] == 'renewed'

    # inactive again -> prompted again; release needs a reason, frees the number, the owner is told
    _backdate(lid)
    assert _prompt() == 1
    v = ws['sup'].get(f'/api/leads/{lid}').json()['version']
    assert ws['sup'].post(f'/api/leads/{lid}/protection', json={'action': 'release', 'version': v}).status_code == 400
    r = ws['sup'].post(f'/api/leads/{lid}/protection', json={'action': 'release', 'reason': 'No response in 3 months', 'version': v})
    assert r.status_code == 200, r.text
    assert r.json()['stage'] == 'lost' and r.json()['lost_reason'] == 'Released after protection review: No response in 3 months'
    assert r.json()['protection'] is None
    assert _flags(ws, lid, 'resolved')[0]['resolution'] == 'released'
    with system_tx() as c:
        assert c.execute(text('SELECT count(*) FROM lead_ownership_claims WHERE lead_id = :l'), {'l': lid}).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM notifications WHERE kind = 'lead.released' AND payload->>'lead_id' = :l"), {'l': lid}).scalar_one() == 1
        assert c.execute(text("SELECT count(*) FROM audit_log WHERE action IN ('lead.protection_renewed', 'lead.protection_released') "
                              "AND resource_id = :l"), {'l': lid}).scalar_one() == 2
    assert ws['sup'].post(f'/api/leads/{lid}/protection', json={'action': 'release', 'reason': 'again', 'version': r.json()['version']}).status_code == 409


def test_lead_with_scheduled_meeting_is_never_released(ws):
    _act, _sid, lead = _make_lead(ws, new_phone())
    lid = lead['id']
    from tfsc_crm.db.session import system_tx
    with system_tx() as c:
        c.execute(text("""INSERT INTO meetings (id, org_id, lead_id, organizer_id, title, starts_at, ends_at, status)
                          SELECT gen_random_uuid(), org_id, id, (SELECT id FROM users WHERE email = 'sup@crm.test'), 'Demo', now() + interval '1 day',
                                 now() + interval '1 day 30 minutes', 'scheduled' FROM leads WHERE id = :l"""), {'l': lid})
    _backdate(lid)
    assert _prompt() == 0  # not prompted while a meeting is scheduled
    v = ws['sup'].get(f'/api/leads/{lid}').json()['version']
    r = ws['sup'].post(f'/api/leads/{lid}/protection', json={'action': 'release', 'reason': 'stale', 'version': v})
    assert r.status_code == 409 and 'scheduled meeting' in r.text
    assert ws['sup'].get(f'/api/leads/{lid}').json()['stage'] == 'qualified_lead'

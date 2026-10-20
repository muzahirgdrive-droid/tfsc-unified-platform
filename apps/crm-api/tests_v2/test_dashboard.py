"""P5: metric definitions, ledger/rollup consistency, role-specific dashboards, layouts (§20, §23)."""
from datetime import timedelta

from sqlalchemy import text

from .conftest import new_phone


def test_summary_matches_definitions_and_ladder(ws):
    from tfsc_crm.db.session import system_tx
    a = ws['c']  # isolated agent under sup2
    before = a.get('/api/dashboard/summary?preset=today&scope=me').json()['current']
    rows = [
        {'name': 'Kpi One', 'phone': new_phone(), 'called': True},                                          # 10
        {'name': 'Kpi Two', 'phone': new_phone(), 'called': True, 'connected': True},                       # 20
        {'name': 'Kpi Three', 'phone': new_phone(), 'called': True, 'connected': True, 'email_sent': True},  # 30
        {'name': 'Kpi Four', 'phone': new_phone(), 'called': False},                                         # no attempt
        {'name': 'Kpi Five', 'phone': new_phone()},                                                          # research
    ]
    for r in rows:
        assert a.post('/api/activities', json=r).status_code == 200
    cur = a.get('/api/dashboard/summary?preset=today&scope=me').json()
    now = cur['current']
    assert now['attempts'] - before['attempts'] == 3
    assert now['connected'] - before['connected'] == 2
    assert now['emails'] - before['emails'] == 1 and now['any_touch'] - before['any_touch'] == 1 and now['sms'] - before['sms'] == 0
    assert now['research'] - before['research'] == 1
    assert now['points'] - before['points'] == 60
    assert 'Σ points' in cur['definitions']['quality']
    # nightly reconciliation finds no drift between raw activities and rollups
    from tfsc_crm.db.session import tx
    from tfsc_crm.domain import rollups
    from tfsc_crm.pacific import business_date
    with system_tx() as c:
        org = c.execute(text("SELECT org_id FROM users WHERE email='c@crm.test'")).scalar_one()
    with tx(org) as c:
        assert rollups.reconcile(c, org, business_date(), fix=False) == []


def test_agents_only_see_themselves_but_everyone_sees_leaderboard(ws):
    a = ws['a']
    me = a.get('/api/dashboard/summary?preset=today&scope=org').json()
    mine = a.get('/api/dashboard/summary?preset=today&scope=me').json()
    assert me['current'] == mine['current']  # agent cannot widen scope
    lb = a.get('/api/dashboard/leaderboard?preset=today').json()
    assert lb['total'] >= 3 and lb['me'] is not None
    names = {r['name'] for r in lb['items']}
    assert ws['c'].user['display_name'] in names
    for r in lb['items']:
        assert set(r) == {'user_id', 'name', 'has_photo', 'attempts', 'conn_rate', 'quality', 'calls_per_day', 'composite', 'band', 'points', 'rank'}
    assert a.get('/api/dashboard/team-board').status_code == 403
    assert a.get(f"/api/dashboard/summary?agent_id={ws['b'].id}").status_code == 404


def test_supervisor_board_funnel_heatmap_layout(ws):
    sup = ws['sup']
    board = sup.get('/api/dashboard/team-board').json()
    names = {x['name'] for x in board['agents']}
    assert ws['a'].user['display_name'] in names and ws['c'].user['display_name'] not in names  # scope
    assert {s['stage'] for s in sup.get('/api/dashboard/funnel?preset=mtd').json()['stages']} >= {'Attempts', 'Lead', 'Signed'}
    assert sup.get('/api/dashboard/heatmap?preset=mtd').status_code == 200
    lay = sup.get('/api/dashboard/layout').json()
    assert lay['customizable'] and 'team_board' in lay['allowed_widgets'] and 'system_health' not in lay['allowed_widgets']
    assert sup.put('/api/dashboard/layout', json={'widgets': [{'id': 'system_health', 'x': 0, 'y': 0, 'w': 4, 'h': 4}]}).status_code == 400
    r = sup.put('/api/dashboard/layout', json={'widgets': [{'id': 'funnel', 'x': 0, 'y': 0, 'w': 6, 'h': 4}]})
    assert r.status_code == 200 and sup.get('/api/dashboard/layout').json()['source'] == 'saved'
    assert ws['a'].put('/api/dashboard/layout', json={'widgets': []}).status_code == 403
    gl = ws['a'].get('/api/dashboard/layout').json()
    assert not gl['customizable'] and 'group_board' in [w['id'] for w in gl['widgets']]


def test_management_views(ws):
    assert ws['manager'].get('/api/dashboard/supervisors?preset=mtd').status_code == 200
    assert ws['sup'].get('/api/dashboard/supervisors').status_code == 403
    br = ws['director'].get('/api/dashboard/breakdown?by=project&preset=mtd').json()
    assert any(i['key'] == 'P0007' for i in br['items'])
    assert ws['director'].get('/api/dashboard/system-health').status_code == 403
    h = ws['super'].get('/api/dashboard/system-health').json()
    assert 'outbox' in h and 'database_bytes' in h


def test_targets_are_effective_dated(ws):
    from tfsc_crm.pacific import business_date
    r = ws['director'].post('/api/settings/targets', json={'call_target': 150, 'conn_target': 0.5})
    assert r.json()['effective_date'] == (business_date() + timedelta(days=1)).isoformat()
    today = ws['a'].get('/api/dashboard/summary?preset=today&scope=me').json()['current']['targets']
    assert today['call_target'] == 170  # change applies from the next Pacific date only

"""Authorization matrix (§6.2, §26), RLS defense in depth (ADR-0007) and agent leak scan."""
import re
import uuid

import pytest
from sqlalchemy import text

from .conftest import new_phone

RANKS = ['a', 'sup', 'manager', 'director', 'super']  # a = agent (also group leader)
RANK_OF = {k: i for i, k in enumerate(RANKS)}
X = str(uuid.UUID(int=1))

# Every OpenAPI operation must appear here: (method, path) -> (minimum role allowed, sample body).
# 'any' = every signed-in user; 'public' = no session needed.
MATRIX = {
    ('GET', '/api/health'): ('public', None),
    ('GET', '/api/time'): ('public', None),
    ('GET', '/api/auth/status'): ('public', None),
    ('POST', '/api/auth/setup'): ('public', None),
    ('POST', '/api/auth/login'): ('public', None),
    ('POST', '/api/auth/logout'): ('public', None),
    ('GET', '/api/auth/me'): ('any', None),
    ('POST', '/api/auth/password'): ('any', None),
    ('GET', '/api/auth/sessions'): ('any', None),
    ('DELETE', '/api/auth/sessions/{sid}'): ('any', None),
    ('GET', '/api/users'): ('sup', None),
    ('POST', '/api/users'): ('manager', {'first_name': 'N', 'last_name': 'U', 'email': 'matrix-{n}@crm.test', 'crm_role': 'sales_agent', 'temporary_password': 'Synthetic-test-password-512!'}),
    ('GET', '/api/users/{user_id}'): ('any', None),
    ('PATCH', '/api/users/{user_id}'): ('any', None),
    ('POST', '/api/users/request'): ('sup', {'first_name': 'R', 'last_name': 'Q', 'email': 'req@crm.test'}),
    ('POST', '/api/users/{user_id}/photo'): ('any', None),
    ('GET', '/api/users/{user_id}/photo'): ('any', None),
    ('GET', '/api/teams'): ('any', None),
    ('POST', '/api/teams'): ('sup', {'name': 'Matrix', 'kind': 'group', 'supervisor_id': X}),
    ('PATCH', '/api/teams/{team_id}'): ('sup', {'version': 1}),
    ('POST', '/api/teams/{team_id}/members'): ('sup', {'user_id': X}),
    ('POST', '/api/teams/{team_id}/members/remove'): ('sup', {'user_id': X}),
    ('GET', '/api/people/tree'): ('sup', None),
    ('GET', '/api/settings/domains'): ('manager', None),
    ('POST', '/api/settings/domains'): ('director', {'id': 'matrix_dom_{n}', 'name': 'Matrix Domain {n}'}),
    ('PATCH', '/api/settings/domains/{domain_id}'): ('director', {'version': 999}),
    ('GET', '/api/settings/projects'): ('manager', None),
    ('GET', '/api/settings/projects/next-id'): ('director', None),
    ('POST', '/api/settings/projects'): ('director', {'id': 'P9{n:03d}'}),
    ('PATCH', '/api/settings/projects/{project_id}'): ('director', {'version': 999}),
    ('GET', '/api/settings/projects/{project_id}/summary'): ('manager', None),
    ('GET', '/api/settings'): ('director', None),
    ('PATCH', '/api/settings'): ('director', {'key': 'lead_protection_days', 'value': 90}),
    ('GET', '/api/settings/targets'): ('any', None),
    ('POST', '/api/settings/targets'): ('director', {'call_target': 170, 'conn_target': 0.6}),
    ('POST', '/api/activities'): ('any', None),
    ('GET', '/api/activities'): ('any', None),
    ('GET', '/api/activities/{activity_id}'): ('any', None),
    ('PATCH', '/api/activities/{activity_id}'): ('any', None),
    ('POST', '/api/activities/{activity_id}/delete'): ('sup', {'reason': 'matrix test'}),
    ('POST', '/api/activities/{activity_id}/restore'): ('sup', {'reason': 'matrix test'}),
    ('GET', '/api/activities/{activity_id}/revisions'): ('any', None),
    ('POST', '/api/refine'): ('any', None),
    ('POST', '/api/activities/batch'): ('any', None),
    ('GET', '/api/precheck'): ('any', None),
    ('GET', '/api/activities-summary/today'): ('any', None),
    ('GET', '/api/followups'): ('any', None),
    ('POST', '/api/followups/{followup_id}/action'): ('any', None),
    ('POST', '/api/activities/{activity_id}/strong'): ('any', None),
    ('GET', '/api/strong'): ('any', None),
    ('POST', '/api/strong/{sid}/decide'): ('sup', {'version': 1, 'decision': 'confirmed'}),
    ('POST', '/api/strong/{sid}/comments'): ('any', None),
    ('POST', '/api/strong/{sid}/convert'): ('sup', {}),
    ('GET', '/api/leads'): ('any', None),
    ('GET', '/api/leads/{lead_id}'): ('any', None),
    ('POST', '/api/leads/{lead_id}/transition'): ('sup', {'version': 1, 'action': 'sign_up'}),
    ('POST', '/api/leads/{lead_id}/reassign'): ('sup', {'version': 1, 'to_user': X, 'reason': 'matrix', 'incentive_split': {X: 1.0}}),
    ('GET', '/api/flags'): ('sup', None),
    ('POST', '/api/flags/{flag_id}/resolve'): ('sup', {'version': 1, 'resolution': 'data_error', 'note': 'matrix'}),
    ('POST', '/api/flags/ack'): ('sup', {'ids': [X]}),
    ('GET', '/api/notifications'): ('any', None),
    ('POST', '/api/notifications/read'): ('any', None),
    ('GET', '/api/celebrations'): ('any', None),
    ('POST', '/api/leads/{lead_id}/meetings'): ('sup', {'title': 'x', 'starts_at': '2026-11-02T18:00:00Z'}),
    ('GET', '/api/meetings'): ('sup', None),
    ('GET', '/api/users/{user_id}/agreements'): ('director', None),
    ('PUT', '/api/users/{user_id}/agreements'): ('director', {'enabled': False}),
    ('GET', '/api/agreements/launch'): ('any', None),
    ('GET', '/api/leads/{lead_id}/agreements/preflight'): ('sup', None),
    ('POST', '/api/leads/{lead_id}/agreements/handoff'): ('sup', {'legal_name': 'x', 'effective_date': '2026-10-20', 'entity': 'SCRIBE ALIGN LLC'}),
    ('GET', '/api/clients'): ('any', None),
    ('GET', '/api/dashboard/summary'): ('any', None),
    ('GET', '/api/dashboard/leaderboard'): ('any', None),
    ('GET', '/api/dashboard/hourly'): ('any', None),
    ('GET', '/api/dashboard/series'): ('any', None),
    ('GET', '/api/dashboard/team-board'): ('sup', None),
    ('GET', '/api/dashboard/funnel'): ('sup', None),
    ('GET', '/api/dashboard/heatmap'): ('sup', None),
    ('GET', '/api/dashboard/hunting-vs-outreach'): ('sup', None),
    ('GET', '/api/dashboard/supervisors'): ('manager', None),
    ('GET', '/api/dashboard/breakdown'): ('manager', None),
    ('GET', '/api/dashboard/system-health'): ('super', None),
    ('GET', '/api/dashboard/layout'): ('any', None),
    ('PUT', '/api/dashboard/layout'): ('sup', {'widgets': []}),
    ('DELETE', '/api/dashboard/layout'): ('sup', None),
    ('GET', '/api/dashboard/definitions'): ('any', None),
    ('GET', '/api/clients/{client_id}'): ('any', None),
    ('PATCH', '/api/meetings/{meeting_id}'): ('sup', {'version': 1}),
    ('GET', '/api/integrations/google/status'): ('sup', None),
    ('POST', '/api/integrations/google/disconnect'): ('sup', None),
    ('POST', '/api/meetings/sync'): ('sup', None),
}


def _operations(app):
    spec = app.openapi()
    for path, ops in spec['paths'].items():
        for method in ops:
            yield method.upper(), path


def test_every_endpoint_is_classified(ws):
    ops = set(_operations(ws['app']))
    extra = {k for k in MATRIX if k[0] != 'GET' or True} - ops
    missing = ops - set(MATRIX)
    from tests_v2 import test_security as me
    assert not missing, f'Unclassified endpoints (add them to MATRIX with a negative test): {sorted(missing)}'
    assert not (extra - ops), sorted(extra - ops)
    assert me


@pytest.mark.parametrize(('op', 'rule'), [(k, v) for k, v in MATRIX.items() if v[0] not in ('public', 'any')], ids=lambda x: str(x))
def test_negative_authorization(ws, op, rule):
    """Every role below the minimum gets 403/404 (never 2xx) — one negative test per ✗ in §6.2."""
    method, path = op
    min_role, body = rule
    url = re.sub(r'\{[a-z_]+\}', X, path)
    for n, role in enumerate(RANKS[:RANK_OF[min_role]]):
        b = None
        if body is not None:
            b = {k: (v.format(n=n) if isinstance(v, str) else v) for k, v in body.items()}
        r = ws[role].request(method, url, json=b) if b is not None else ws[role].request(method, url)
        assert r.status_code in (403, 404), (role, method, path, r.status_code, r.text[:200])


def test_anonymous_is_rejected(ws):
    from fastapi.testclient import TestClient
    anon = TestClient(ws['app'], base_url='http://testserver')
    for (method, path), (rule, _b) in MATRIX.items():
        if rule == 'public' or method != 'GET':
            continue
        r = anon.request(method, re.sub(r'\{[a-z_]+\}', X, path))
        assert r.status_code == 401, (method, path, r.status_code)


def test_csrf_required_for_mutations(ws):
    from fastapi.testclient import TestClient
    c = TestClient(ws['app'], base_url='http://testserver')
    c.cookies = ws['a'].http.cookies
    r = c.post('/api/activities', json={'name': 'No CSRF'})
    assert r.status_code == 403


def test_cross_origin_rejected(ws):
    r = ws['a'].post('/api/activities', json={'name': 'Cross'}, headers={'Origin': 'https://evil.example'})
    assert r.status_code == 403


def test_role_grant_limits(ws):
    body = lambda role, n: {'first_name': 'G', 'last_name': 'L', 'email': f'grant-{role}-{n}@crm.test', 'crm_role': role}  # noqa: E731
    assert ws['manager'].post('/api/users', json=body('supervisor', 1)).status_code == 403
    assert ws['director'].post('/api/users', json=body('director_admin', 2)).status_code == 403
    assert ws['director'].post('/api/users', json=body('manager', 3)).status_code == 200
    assert ws['super'].post('/api/users', json=body('director_admin', 4)).status_code == 200
    assert ws['sup'].post('/api/users', json=body('sales_agent', 5)).status_code == 403


def test_rls_isolates_orgs(ws):
    from tfsc_crm.db.session import system_tx, tx
    from tfsc_crm.ids import uuid7
    other_org, other_user = uuid7(), uuid7()
    with system_tx() as c:
        c.execute(text("INSERT INTO orgs (id, name) VALUES (:o, 'Other Org')"), {'o': other_org})
        c.execute(text("INSERT INTO users (id, org_id, email, display_name, crm_role) VALUES (:u, :o, 'other-org@crm.test', 'Other', 'sales_agent')"),
                  {'u': other_user, 'o': other_org})
        c.execute(text("INSERT INTO activities (id, org_id, owner_id, business_date, entry_source, provider_name) VALUES (:i, :o, :u, CURRENT_DATE, 'manual', 'Other Org Practice')"),
                  {'i': uuid7(), 'o': other_org, 'u': other_user})
        org1 = c.execute(text("SELECT org_id FROM users WHERE email='super@crm.test'")).scalar_one()
    with tx(org1) as c:
        names = [r[0] for r in c.execute(text('SELECT provider_name FROM activities'))]
        assert 'Other Org Practice' not in names
        assert c.execute(text("SELECT count(*) FROM users WHERE email='other-org@crm.test'")).scalar_one() == 0
        with pytest.raises(Exception):
            with c.begin_nested():
                c.execute(text("INSERT INTO flags (id, org_id, severity, kind) VALUES (gen_random_uuid(), :o, 'info', 'x')"), {'o': other_org})
    with tx('') as c:  # no org set -> nothing visible
        assert c.execute(text('SELECT count(*) FROM activities')).scalar_one() == 0


FORBIDDEN_AGENT_KEYS = ('domain_id', 'project_id', 'project_owner', 'owner_email', 'meet_url', 'attendees', '"meetings"', 'routing_view')


def test_agent_responses_never_contain_routing_or_meeting_fields(ws):
    a = ws['a']
    act = a.post('/api/activities', json={'name': 'Leak Scan Clinic', 'phone': new_phone(), 'called': True, 'followup_needed': True}).json()
    blobs = [str(act)]
    for path in ['/api/auth/me', '/api/activities', '/api/followups', '/api/followups?view=group', '/api/leads', '/api/strong', '/api/notifications',
                 '/api/teams', f"/api/users/{a.id}", f"/api/activities/{act['activity']['id']}", '/api/activities-summary/today',
                 "/api/precheck?phone=4155550000"]:
        r = a.get(path)
        assert r.status_code == 200, (path, r.status_code)
        blobs.append(r.text)
    for blob in blobs:
        for key in FORBIDDEN_AGENT_KEYS:
            assert key not in blob, key

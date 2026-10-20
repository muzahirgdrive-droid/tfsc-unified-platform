"""P7 security: audit verification + anchors, scheduler idempotency, SLA escalations, offboarding, exports."""
import io
from datetime import timedelta

import pytest
from sqlalchemy import text

from .conftest import PW, Client, new_phone


def _sys():
    from tfsc_crm.db.session import system_tx
    return system_tx()


def _org():
    with _sys() as c:
        return c.execute(text("SELECT org_id FROM users WHERE email = 'super@crm.test'")).scalar_one()


def _agent(ws, key: str) -> Client:
    r = ws['manager'].post('/api/users', json={'first_name': key.capitalize(), 'last_name': 'Leaver', 'email': f'{key}@crm.test',
                                               'crm_role': 'sales_agent', 'manager_id': ws['sup'].id, 'temporary_password': PW})
    assert r.status_code == 200, r.text
    c = Client(ws['app']).login(f'{key}@crm.test')
    assert c.post('/api/auth/password', json={'current_password': PW, 'new_password': PW + 'x'}).status_code == 200
    c.post('/api/auth/password', json={'current_password': PW + 'x', 'new_password': PW})
    return Client(ws['app']).login(f'{key}@crm.test')


# ------------------------------------------------------------------ audit chain
def test_audit_verification_detects_edits_and_anchor_rewrites(ws):
    from tfsc_crm.pacific import utcnow
    from tfsc_crm.workers import scheduler
    sup = ws['super']
    r = sup.post('/api/security/audit/verify')
    assert r.status_code == 200 and r.json()['ok'] is True, r.text
    assert ws['director'].post('/api/security/audit/verify').status_code == 403  # Director: read only

    # Daily anchor through the scheduler; a second run in the same period does nothing.
    now = utcnow().replace(hour=9)  # 01:00-02:00 PT: the daily anchor is due
    first = scheduler.run_due(now, only={'audit_anchor'})
    assert first['audit_anchor']['status'] == 'ok'
    assert scheduler.run_due(now, only={'audit_anchor'}) == {}
    anchors = ws['director'].get('/api/security/anchors').json()
    assert anchors and anchors[0]['sink'] == 'file'
    org = _org()
    with _sys() as c:
        anchored_id = c.execute(text('SELECT last_audit_id FROM audit_anchors WHERE org_id = :o ORDER BY id DESC LIMIT 1'), {'o': org}).scalar_one()
        row = c.execute(text('SELECT id, after FROM audit_log WHERE org_id = :o AND id <= :i AND resource NOT IN (\'pilot\', \'migration\') '
                             'ORDER BY id DESC LIMIT 1'), {'o': org, 'i': anchored_id}).first()
        target, original = row[0], row[1]
        c.execute(text("UPDATE audit_log SET after = '{\"tampered\": true}'::jsonb WHERE id = :i"), {'i': target})
    try:
        res = sup.post('/api/security/audit/verify').json()
        assert res['ok'] is False and target in res['altered_rows']
    finally:
        with _sys() as c:
            c.execute(text('UPDATE audit_log SET after = CAST(:a AS jsonb) WHERE id = :i'),
                      {'a': None if original is None else __import__('json').dumps(original), 'i': target})
    # Re-hashing a row to hide an edit breaks the link to the next row and the anchor.
    with _sys() as c:
        h = c.execute(text('SELECT hash FROM audit_log WHERE id = :i'), {'i': anchored_id}).scalar_one()
        c.execute(text("UPDATE audit_log SET hash = repeat('f', 64) WHERE id = :i"), {'i': anchored_id})
    try:
        res = sup.post('/api/security/audit/verify').json()
        assert res['ok'] is False and res['anchors_failed']
    finally:
        with _sys() as c:
            c.execute(text('UPDATE audit_log SET hash = :h WHERE id = :i'), {'h': h, 'i': anchored_id})
    assert sup.post('/api/security/audit/verify').json()['ok'] is True


def test_app_role_cannot_rewrite_audit_or_anchors(ws):
    from sqlalchemy.exc import DBAPIError

    from tfsc_crm.db.session import tx
    org = _org()
    for stmt in ("UPDATE audit_log SET action = 'x'", 'DELETE FROM audit_log', "UPDATE audit_anchors SET head_hash = 'x'", 'DELETE FROM audit_anchors'):
        with pytest.raises(DBAPIError), tx(org) as c:
            c.execute(text(stmt))


# ------------------------------------------------------------------ escalations
def test_red_flag_sla_and_overdue_followups_escalate_once(ws):
    from tfsc_crm.pacific import utcnow
    from tfsc_crm.workers import scheduler
    org = _org()
    now = utcnow()
    with _sys() as c:
        fid = c.execute(text("INSERT INTO flags (id, org_id, severity, kind, raised_for_user, assigned_to_supervisor, created_at, updated_at) "
                             "VALUES (gen_random_uuid(), :o, 'red', 'duplicate_lead', :b, :s, :t, :t) RETURNING id"),
                        {'o': org, 'b': ws['b'].id, 's': ws['sup'].id, 't': now - timedelta(hours=25)}).scalar_one()
        aid = c.execute(text('SELECT gen_random_uuid()')).scalar_one()
        c.execute(text("INSERT INTO followups (id, org_id, activity_id, activity_date, owner_id, due_at, status, created_at, updated_at) "
                       "VALUES (gen_random_uuid(), :o, :a, CURRENT_DATE, :b, :d, 'open', :d, :d)"),
                  {'o': org, 'a': aid, 'b': ws['b'].id, 'd': now - timedelta(days=4)})
    before = {k: _notes(ws[k]) for k in ('manager', 'super', 'sup')}
    ran = scheduler.run_due(now, only={'flag_sla', 'followups_overdue'})
    assert ran['flag_sla']['escalated'] >= 1 and ran['followups_overdue']['escalated'] >= 1
    assert _notes(ws['manager'])['flag.escalated'] == before['manager'].get('flag.escalated', 0) + 1
    assert _notes(ws['super'])['flag.escalated'] == before['super'].get('flag.escalated', 0) + 1
    assert _notes(ws['sup'])['followup.overdue'] == before['sup'].get('followup.overdue', 0) + 1
    with _sys() as c:
        assert c.execute(text('SELECT escalated_at IS NOT NULL FROM flags WHERE id = :f'), {'f': fid}).scalar_one()
    later = now + timedelta(hours=2)  # next periods: nothing escalates twice
    scheduler.run_due(later, only={'flag_sla', 'followups_overdue'})
    assert _notes(ws['manager'])['flag.escalated'] == before['manager'].get('flag.escalated', 0) + 1
    assert _notes(ws['sup'])['followup.overdue'] == before['sup'].get('followup.overdue', 0) + 1


def _notes(client) -> dict:
    items = client.get('/api/notifications?limit=200').json()
    items = items['items'] if isinstance(items, dict) else items
    out: dict[str, int] = {}
    for n in items:
        out[n['kind']] = out.get(n['kind'], 0) + 1
    return out


# ------------------------------------------------------------------ offboarding
def test_offboarding_suspends_everything_and_moves_work(ws):
    from .test_api_pipeline import _make_lead
    leaver = _agent(ws, 'leaver1')
    ws_local = {**ws, 'leaver1': leaver}
    _act, _sid, lead = _make_lead(ws_local, new_phone(), agent='leaver1')
    leaver.post('/api/activities', json={'name': 'Follow Up Clinic', 'phone': new_phone(), 'called': True, 'connected': True,
                                         'next_followup': '2099-01-05'})
    assert ws['sup'].post(f'/api/users/{leaver.id}/offboard', json={'reason': 'resigned'}).status_code == 403  # Manager+ only
    r = ws['manager'].post(f'/api/users/{leaver.id}/offboard', json={'reason': 'resigned'})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s['leads_moved'] == 1 and s['followups_moved'] >= 1 and s['sessions_revoked'] >= 1 and s['queue_owner'] == ws['sup'].id
    assert leaver.get('/api/auth/me').status_code == 401
    assert Client(ws['app']).http.post('/api/auth/login', json={'email': 'leaver1@crm.test', 'password': PW}).status_code == 401
    with _sys() as c:
        owner = c.execute(text('SELECT owner_id FROM leads WHERE id = :l'), {'l': lead['id']}).scalar_one()
        moved = c.execute(text("SELECT count(*) FROM lead_assignments WHERE lead_id = :l AND reason LIKE 'Offboarding:%'"), {'l': lead['id']}).scalar_one()
        status = c.execute(text('SELECT status, left_on IS NOT NULL FROM users WHERE id = :u'), {'u': leaver.id}).one()
    assert str(owner) == ws['sup'].id and moved == 1 and tuple(status) == ('offboarded', True)
    assert ws['manager'].post(f'/api/users/{leaver.id}/offboard', json={'reason': 'again'}).status_code == 409


# ------------------------------------------------------------------ exports
def test_export_requires_approval_and_is_watermarked_encrypted_and_formula_safe(ws):
    import pyzipper

    from tfsc_crm.pacific import business_date
    ws['b'].post('/api/activities', json={'name': 'Formula Clinic', 'phone': new_phone(), 'called': True, 'remarks': '=HYPERLINK("http://x","y")'})
    today = business_date().isoformat()
    body = {'kind': 'activities', 'start': today, 'end': today, 'reason': 'Monthly compliance review'}
    for role in ('a', 'sup'):
        assert ws[role].post('/api/exports', json=body).status_code == 403
    r = ws['manager'].post('/api/exports', json=body)
    assert r.status_code == 201, r.text
    eid = r.json()['id']
    assert ws['manager'].post(f'/api/exports/{eid}/decision', json={'approve': True}).status_code == 403
    assert ws['manager'].get(f'/api/exports/{eid}/download').status_code == 410  # not approved yet
    r = ws['director'].post(f'/api/exports/{eid}/decision', json={'approve': True})
    assert r.status_code == 200 and r.json()['status'] == 'ready' and r.json()['row_count'] > 0, r.text
    assert ws['director'].post(f'/api/exports/{eid}/password').status_code == 403  # only the requester
    pw = ws['manager'].post(f'/api/exports/{eid}/password')
    assert pw.status_code == 200
    assert ws['manager'].post(f'/api/exports/{eid}/password').status_code == 409  # shown once
    dl = ws['manager'].get(f'/api/exports/{eid}/download')
    assert dl.status_code == 200 and dl.headers['content-type'] == 'application/zip'
    with pyzipper.AESZipFile(io.BytesIO(dl.content)) as z:
        name = z.namelist()[0]
        with pytest.raises(RuntimeError):
            z.read(name)  # encrypted
        z.setpassword(pw.json()['password'].encode())
        csv_text = z.read(name).decode('utf-8-sig')
    first, header = csv_text.splitlines()[:2]
    assert eid in first and 'Manager Test' in first and 'Director Test' in first
    assert header.startswith('date,agent,provider')
    assert '"\'=HYPERLINK' in csv_text and '\n=HYPERLINK' not in csv_text and ',=HYPERLINK' not in csv_text
    assert 'meet' not in header.lower()
    with _sys() as c:
        acts = [r[0] for r in c.execute(text("SELECT action FROM audit_log WHERE resource = 'export' AND resource_id = :e ORDER BY id"), {'e': eid})]
    assert acts == ['export.requested', 'export.approved', 'export.password_revealed', 'export.downloaded']
    listed = ws['manager'].get('/api/exports').json()
    assert any(x['id'] == eid and x['password_revealed'] and x['downloads'] == 1 for x in listed)
    r2 = ws['manager'].post('/api/exports', json={**body, 'reason': 'Second request'}).json()
    assert ws['director'].post(f"/api/exports/{r2['id']}/decision", json={'approve': False, 'note': 'not needed'}).json()['status'] == 'rejected'


# ------------------------------------------------------------------ overview + sessions
def test_security_overview_and_session_revocation(ws):
    Client(ws['app']).http.post('/api/auth/login', json={'email': 'b@crm.test', 'password': 'wrong-password-123'})
    assert ws['manager'].get('/api/security/overview').status_code == 403
    r = ws['director'].get('/api/security/overview')
    assert r.status_code == 200, r.text
    ov = r.json()
    assert ov['failed_logins_24h'] >= 1 and ov['active_sessions'] >= 1 and ov['audit_rows'] > 0
    victim = Client(ws['app']).login('c@crm.test')
    sessions = ws['director'].get('/api/security/sessions', params={'user_id': victim.id}).json()
    assert sessions
    assert ws['director'].delete(f"/api/security/sessions/{sessions[0]['id']}").status_code == 403  # Director: read only
    for s in sessions:
        assert ws['super'].delete(f"/api/security/sessions/{s['id']}").status_code == 200
    assert victim.get('/api/auth/me').status_code == 401
    ws['c'] = Client(ws['app']).login('c@crm.test')  # keep the shared fixture usable
    page = ws['director'].get('/api/security/audit', params={'action': 'session.'}).json()
    assert page['items'] and page['items'][0]['action'] == 'session.revoked'

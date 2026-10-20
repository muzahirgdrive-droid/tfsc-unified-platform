"""P4: celebration fan-out, SSE catch-up, meetings with the Google adapter (mock provider) — acceptance 10-11."""
import asyncio
import os
import threading
import time
from datetime import UTC, datetime, timedelta

from .conftest import new_phone
from .test_api_pipeline import _make_lead


def _collect(org_id: str, out: list, ready: threading.Event, stop: threading.Event):
    from tfsc_crm.realtime import broker

    async def run():
        sub = broker().subscribe(org_id).__aiter__()
        pending = asyncio.ensure_future(sub.__anext__())  # never cancel: cancelling closes the generator
        await asyncio.sleep(0)
        ready.set()
        while not stop.is_set():
            done, _ = await asyncio.wait({pending}, timeout=0.05)
            if done:
                out.append(pending.result())
                pending = asyncio.ensure_future(sub.__anext__())
        pending.cancel()
    asyncio.run(run())


def test_celebration_reaches_every_connected_client_within_3s(ws):
    from sqlalchemy import text

    from tfsc_crm.db.session import system_tx
    from tfsc_crm.workers import dispatcher
    dispatcher.process_once()  # drain anything older
    with system_tx() as c:
        org = str(c.execute(text("SELECT org_id FROM users WHERE email='a@crm.test'")).scalar_one())
    clients = [([], threading.Event(), threading.Event()) for _ in range(5)]
    threads = [threading.Thread(target=_collect, args=(org, *cl), daemon=True) for cl in clients]
    for t in threads:
        t.start()
    for _, ready, _ in clients:
        assert ready.wait(5)
    _act, _sid, lead = _make_lead(ws, new_phone())
    detail = ws['sup'].get(f"/api/leads/{lead['id']}").json()
    t0 = time.perf_counter()
    r = ws['sup'].post(f"/api/leads/{lead['id']}/transition", json={'version': detail['version'], 'action': 'sign_up'})
    assert r.status_code == 200, r.text
    dispatcher.process_once()
    deadline = time.time() + 3
    while time.time() < deadline and not all(any(e['topic'] == 'sale.celebrated' for e in out) for out, _, _ in clients):
        time.sleep(0.02)
    elapsed = time.perf_counter() - t0
    for out, _, stop in clients:
        stop.set()
        ev = next(e for e in out if e['topic'] == 'sale.celebrated')
        assert 'Atest' in ev['message'].replace(' ', '') or ws['a'].user['display_name'] in ev['message']
        assert ws['sup'].user['display_name'] in ev['message']
        assert 'Lead Practice' not in str(ev)  # no client name or details
    assert elapsed < 3, elapsed
    fame = ws['b'].get('/api/celebrations').json()
    assert fame['recent'] and fame['recent'][0]['agent'] == ws['a'].user['display_name']


def test_sse_backlog_shows_recent_celebration_to_late_login(ws):
    os.environ['CRM_SSE_HEARTBEAT_S'] = '0.5'
    with ws['c'].http.stream('GET', '/api/events') as resp:
        assert resp.status_code == 200 and resp.headers['content-type'].startswith('text/event-stream')
        seen = ''
        for line in resp.iter_lines():
            seen += line + '\n'
            if 'sale.celebrated' in seen and line.startswith('data:'):
                break
    assert 'Congratulations' in seen


def test_meetings_with_mock_google_and_agent_never_sees_them(ws):
    os.environ['CRM_GOOGLE_MODE'] = 'mock'
    try:
        _act, _sid, lead = _make_lead(ws, new_phone())
        sup, a = ws['sup'], ws['a']
        assert a.post(f"/api/leads/{lead['id']}/meetings", json={'title': 'Demo', 'starts_at': '2026-11-02T18:00:00Z'}).status_code == 403
        r = sup.post(f"/api/leads/{lead['id']}/meetings", json={'title': 'Demo call', 'starts_at': '2026-11-02T18:00:00Z', 'duration_minutes': 45,
                                                               'attendees': ['Owner@Clinic.test']})
        assert r.status_code == 200, r.text
        mtg = r.json()
        assert mtg['meet_url'].startswith('https://meet.google.com/') and mtg['google_state'] == 'synced'
        assert mtg['attendees'] == ['owner@clinic.test']
        assert sup.get(f"/api/leads/{lead['id']}").json()['stage'] == 'meeting'
        agent_view = a.get(f"/api/leads/{lead['id']}")
        assert 'meet.google' not in agent_view.text and 'Demo call' not in agent_view.text and agent_view.json()['stage'] == 'with_management'
        assert a.get('/api/meetings').status_code == 403
        events = [n for n in a.get('/api/notifications').json()['items'] if 'meet' in str(n['payload'])]
        assert not events
        later = (datetime(2026, 11, 3, 18, tzinfo=UTC)).isoformat()
        r = sup.patch(f"/api/meetings/{mtg['id']}", json={'version': mtg['version'], 'starts_at': later})
        assert r.status_code == 200 and r.json()['ends_at'].startswith('2026-11-03T18:45')
        r = sup.patch(f"/api/meetings/{mtg['id']}", json={'version': r.json()['version'], 'status': 'held', 'notes': 'Went well'})
        assert r.json()['status'] == 'held'
        week = sup.get('/api/meetings', params={'date_from': (datetime(2026, 11, 1, tzinfo=UTC)).isoformat(),
                                                 'date_to': (datetime(2026, 11, 1, tzinfo=UTC) + timedelta(days=7)).isoformat()}).json()
        assert any(x['id'] == mtg['id'] for x in week)
    finally:
        os.environ['CRM_GOOGLE_MODE'] = 'off'


def test_meetings_without_google_record_locally_without_fake_link(ws):
    _act, _sid, lead = _make_lead(ws, new_phone())
    st = ws['sup'].get('/api/integrations/google/status').json()
    assert st['configured'] is False and 'not connected' in st['message']
    r = ws['sup'].post(f"/api/leads/{lead['id']}/meetings", json={'title': 'Phone demo', 'starts_at': '2026-11-05T17:00:00Z'})
    assert r.status_code == 200 and r.json()['meet_url'] is None and r.json()['google_state'] == 'not_connected'

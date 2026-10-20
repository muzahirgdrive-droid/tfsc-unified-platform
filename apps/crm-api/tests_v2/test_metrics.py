"""Prometheus /metrics: hidden without a token, bearer-protected, labels are route templates (no ids)."""
from fastapi.testclient import TestClient


def test_metrics_requires_token_and_uses_route_templates(ws, monkeypatch):
    from tfsc_crm.config import get_settings
    ws['a'].get(f"/api/users/{ws['a'].id}")
    anon = TestClient(ws['app'], base_url='http://10.1.2.3:8000')
    assert anon.get('/metrics').status_code == 404  # no token configured -> endpoint does not exist
    monkeypatch.setenv('CRM_METRICS_TOKEN', 'scrape-token-for-tests')
    get_settings.cache_clear()
    try:
        assert anon.get('/metrics').status_code == 404
        assert anon.get('/metrics', headers={'Authorization': 'Bearer wrong'}).status_code == 404
        r = anon.get('/metrics', headers={'Authorization': 'Bearer scrape-token-for-tests'})
        assert r.status_code == 200 and r.headers['content-type'].startswith('text/plain')
        body = r.text
        assert 'crm_http_requests_total{method="GET",route="/api/users/{user_id}",status="200"}' in body
        assert ws['a'].id not in body
        assert 'crm_http_request_duration_seconds_bucket' in body and 'crm_db_up 1' in body and 'crm_outbox_pending' in body
    finally:
        monkeypatch.delenv('CRM_METRICS_TOKEN')
        get_settings.cache_clear()

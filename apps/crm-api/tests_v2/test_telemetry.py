"""Structured logs and error-report scrubbing (§4.2, §23): access lines carry the route template, never the raw path or
query string; request ids are echoed only when well-formed; JSON lines carry the request id; Sentry events lose
cookies, headers, bodies, query strings and user details."""
import json
import logging


class Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        from tfsc_crm.telemetry import JsonFormatter
        self.setFormatter(JsonFormatter())
        self.lines: list[dict] = []

    def emit(self, record):
        self.lines.append(json.loads(self.format(record)))  # formatted inside the request, like the stdout handler


def test_access_lines_are_json_with_route_templates_and_request_ids(ws):
    h = Collect()
    log = logging.getLogger('tfsc_crm.access')
    log.addHandler(h)
    log.setLevel(logging.INFO)
    try:
        lead_id = '01a15b00-0000-7000-8000-000000000000'
        r = ws['sup'].get(f'/api/leads/{lead_id}?phone=4155550101', headers={'X-Request-Id': 'trace-abc-123'})
        assert r.headers['x-request-id'] == 'trace-abc-123'
        bad = ws['sup'].get('/api/leads', headers={'X-Request-Id': '<script>alert(1)</script>'})
        assert bad.headers['x-request-id'] != '<script>alert(1)</script>' and len(bad.headers['x-request-id']) == 32
        ws['sup'].get('/api/health')
    finally:
        log.removeHandler(h)
    first, second = h.lines[0], h.lines[1]
    assert first['msg'] == 'request' and first['route'] == '/api/leads/{lead_id}' and first['status'] == 404
    assert first['request_id'] == 'trace-abc-123' and isinstance(first['duration_ms'], float)
    assert lead_id not in json.dumps(h.lines) and '4155550101' not in json.dumps(h.lines)  # no raw path, no query string
    assert second['route'] == '/api/leads' and second['request_id'] == bad.headers['x-request-id']
    assert all(x['route'] != '/api/health' for x in h.lines)  # health checks are not logged


def test_error_reports_carry_no_personal_data():
    from tfsc_crm.telemetry import _scrub
    event = {'request': {'url': 'https://crm.example.com/api/leads', 'cookies': {'crm_session': 's'}, 'headers': {'Authorization': 'x'},
                         'data': {'name': 'Jane Doe'}, 'query_string': 'phone=4155550101', 'env': {'REMOTE_ADDR': '10.0.0.1'}},
             'user': {'id': 'u1', 'email': 'jane@example.com', 'ip_address': '10.0.0.1'}}
    out = _scrub(event, {})
    assert out['request'] == {'url': 'https://crm.example.com/api/leads'} and out['user'] == {'id': 'u1'}


def test_metrics_add_up_across_uvicorn_workers(tmp_path, monkeypatch):
    """Two workers in one container: a scrape answered by either one reports both workers' requests."""
    import os
    import time

    from tfsc_crm import observability as o
    monkeypatch.setenv('CRM_METRICS_DIR', str(tmp_path))
    sibling = {'count': [['GET', '/api/leads', '200', 7]], 'hist': [['GET', '/api/leads', [1, 2, 3, 4, 5, 6, 7, 7, 7, 7, 7, 7, 7]]],
               'sum': [['GET', '/api/leads', 0.7]], 'sse': 5, 'at': time.time()}
    gone = {**sibling, 'sse': 9, 'at': time.time() - 120}  # an exited worker: counters stay, connections do not
    (tmp_path / '999998.json').write_text(json.dumps(sibling))
    (tmp_path / '999997.json').write_text(json.dumps(gone))
    before = o._count.get(('GET', '/api/leads', '200'), 0)
    o.observe('GET', '/api/leads', 200, 0.05)
    text = o.render({})
    assert f'crm_http_requests_total{{method="GET",route="/api/leads",status="200"}} {before + 1 + 14}' in text
    assert 'crm_sse_connections 5' in text.replace('crm_sse_connections 5.0', 'crm_sse_connections 5')
    assert (tmp_path / f'{os.getpid()}.json').exists()



def test_traces_keep_paths_but_not_query_strings():
    """Search terms (q=...) and sign-in codes (?code=...&state=...) never leave in a span: neither the server span of an
    instrumented FastAPI app (whose middleware sets the URL again after the span starts) nor an outgoing httpx call."""
    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import SyncOpenTelemetryTransport
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from tfsc_crm.telemetry import drop_query_strings, query_string_filter
    provider, out = TracerProvider(), InMemorySpanExporter()
    provider.add_span_processor(query_string_filter())
    provider.add_span_processor(SimpleSpanProcessor(out))
    app = FastAPI()

    @app.get('/api/leads')
    def leads(q: str = '') -> dict:
        return {'n': len(q)}
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, server_request_hook=drop_query_strings)
    try:
        assert TestClient(app).get('/api/leads?q=Jane+Doe&phone=4155550101').json() == {'n': 8}
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
    transport = SyncOpenTelemetryTransport(httpx.MockTransport(lambda r: httpx.Response(200)), tracer_provider=provider)
    httpx.Client(transport=transport).get('https://sso.example.com/token?code=SECRETCODE&state=xyz')
    spans = out.get_finished_spans()
    urls = [v for s in spans for k, v in (s.attributes or {}).items() if k in ('http.url', 'url.full', 'http.target', 'url.query')]
    assert any('/api/leads' in str(u) for u in urls) and any('sso.example.com/token' in str(u) for u in urls)
    dumped = repr([dict(s.attributes or {}) for s in spans])
    assert 'Jane' not in dumped and '4155550101' not in dumped and 'SECRETCODE' not in dumped and 'xyz' not in dumped

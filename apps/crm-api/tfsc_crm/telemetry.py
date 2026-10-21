"""Logs, traces and error reporting (§4.2, §23); metrics live in observability.py.

- Logs: one JSON object per line on stdout (CRM_LOG_FORMAT=json, the default in hosted mode; `text` otherwise) with
  time, level, logger, message, request_id and, when tracing, trace_id/span_id. The API writes one access line per
  request: method, route template (never the raw path), status, duration and request id. No bodies, query strings,
  names, phone numbers or emails are logged.
- Traces: OpenTelemetry over OTLP/HTTP when OTEL_EXPORTER_OTLP_ENDPOINT (or ..._TRACES_ENDPOINT) is set, with the
  standard OTEL_* variables; FastAPI, httpx and every SQL statement are traced (statement text, never bound parameters).
  Span URLs keep the path only: query strings carry search terms and sign-in codes.
- Errors: Sentry when CRM_SENTRY_DSN is set, without personal data (no cookies, headers, bodies or query strings).
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from .config import get_settings

request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar('request_id', default=None)
ACCESS_FIELDS = ('method', 'route', 'status', 'duration_ms')
_state: dict[str, bool] = {}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {'ts': datetime.fromtimestamp(record.created, UTC).isoformat(timespec='milliseconds'),
                               'level': record.levelname.lower(), 'logger': record.name, 'msg': record.getMessage()}
        rid = request_id.get()
        if rid:
            out['request_id'] = rid
        if _state.get('tracing'):
            from opentelemetry import trace
            ctx = trace.get_current_span().get_span_context()
            if ctx.is_valid:
                out['trace_id'], out['span_id'] = f'{ctx.trace_id:032x}', f'{ctx.span_id:016x}'
        for k in ACCESS_FIELDS:
            if hasattr(record, k):
                out[k] = getattr(record, k)
        if record.exc_info:
            out['exc'] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


QUERY_KEYS = ('http.url', 'url.full', 'http.target', 'url.query')


def drop_query_strings(span: Any, scope: Any = None) -> None:
    """Cut the query string off every URL attribute. Runs as each span starts (outgoing httpx calls) and as the
    FastAPI server hook (the ASGI middleware sets the request attributes again after the span has started)."""
    attrs = getattr(span, 'attributes', None) or {}
    for k in QUERY_KEYS:
        v = attrs.get(k)
        if isinstance(v, str) and (k == 'url.query' or '?' in v):
            span.set_attribute(k, '' if k == 'url.query' else v.split('?', 1)[0])


def tracing_enabled() -> bool:
    return bool(os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT') or os.getenv('OTEL_EXPORTER_OTLP_TRACES_ENDPOINT'))


def configure(service: str = 'tfsc-crm') -> None:
    """Idempotent; called by the API factory and by the worker entry points."""
    if _state.get('configured'):
        return
    s = get_settings()
    if s.mode != 'test':  # tests keep pytest's log capture
        handler = logging.StreamHandler(sys.stdout)
        fmt = os.getenv('CRM_LOG_FORMAT', 'json' if s.hosted else 'text')
        handler.setFormatter(JsonFormatter() if fmt == 'json' else logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
        root = logging.getLogger()
        root.handlers[:] = [handler]
        root.setLevel(os.getenv('CRM_LOG_LEVEL', 'INFO').upper())
        for name in ('uvicorn', 'uvicorn.error'):
            logging.getLogger(name).handlers[:] = []
            logging.getLogger(name).propagate = True
        # uvicorn's access lines carry client IPs and raw paths with query strings; ours (tfsc_crm.access) replace them
        logging.getLogger('uvicorn.access').handlers[:] = []
        logging.getLogger('uvicorn.access').propagate = False
    logging.getLogger('httpx').setLevel(logging.WARNING)  # it logs every outgoing URL at INFO
    if tracing_enabled():
        _tracing(service)
    if s.sentry_dsn:
        _sentry(s.sentry_dsn, service)
    _state['configured'] = True


def _tracing(service: str) -> None:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from . import VERSION
    provider = TracerProvider(resource=Resource.create({'service.name': os.getenv('OTEL_SERVICE_NAME', service), 'service.version': VERSION,
                                                        'deployment.environment': get_settings().mode}))
    provider.add_span_processor(query_string_filter())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument()
    _state['tracing'] = True
    from .db import session
    if session._engine is not None:  # created before tracing was configured
        instrument_engine(session._engine)


def query_string_filter() -> Any:
    from opentelemetry.sdk.trace import SpanProcessor

    class DropQueryStrings(SpanProcessor):
        def on_start(self, span: Any, parent_context: Any = None) -> None:
            drop_query_strings(span)
    return DropQueryStrings()


def instrument_engine(engine: Any) -> None:
    """A span per SQL statement (statement text only, never the parameters), from SQLAlchemy's cursor events.
    (opentelemetry-instrumentation-sqlalchemy does not support SQLAlchemy 2.1 yet.)"""
    if _state.get('tracing') and not getattr(engine, '_tfsc_traced', False):
        from opentelemetry import trace
        from opentelemetry.trace import SpanKind, Status, StatusCode
        from sqlalchemy import event
        tracer = trace.get_tracer('tfsc.sql')

        @event.listens_for(engine, 'before_cursor_execute')
        def _start(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
            context._tfsc_span = tracer.start_span((statement.split(None, 1) or ['SQL'])[0].upper(), kind=SpanKind.CLIENT,
                                                   attributes={'db.system': engine.dialect.name, 'db.statement': statement[:2000]})

        @event.listens_for(engine, 'after_cursor_execute')
        def _end(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
            span = getattr(context, '_tfsc_span', None)
            if span is not None:
                span.end()

        @event.listens_for(engine, 'handle_error')
        def _error(ctx):  # type: ignore[no-untyped-def]
            span = getattr(ctx.execution_context, '_tfsc_span', None) if ctx.execution_context is not None else None
            if span is not None:
                span.set_status(Status(StatusCode.ERROR, type(ctx.original_exception).__name__))
                span.end()
        engine._tfsc_traced = True


def instrument_app(app: Any) -> None:
    if _state.get('tracing'):
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        # health checks, scrapes and the long-lived SSE stream would only add noise
        FastAPIInstrumentor.instrument_app(app, excluded_urls='/api/health,/metrics,/api/events', server_request_hook=drop_query_strings)


def _scrub(event: dict, hint: dict) -> dict:
    req = event.get('request') or {}
    for k in ('cookies', 'headers', 'data', 'query_string', 'env'):
        req.pop(k, None)
    if 'user' in event:
        event['user'] = {k: v for k, v in event['user'].items() if k == 'id'}
    return event


def _sentry(dsn: str, service: str) -> None:
    import sentry_sdk

    from . import VERSION
    sentry_sdk.init(dsn=dsn, environment=get_settings().mode, release=f'{service}@{VERSION}', send_default_pii=False,
                    max_request_body_size='never', traces_sample_rate=float(os.getenv('CRM_SENTRY_TRACES_RATE', '0')),
                    before_send=_scrub)  # type: ignore[arg-type]

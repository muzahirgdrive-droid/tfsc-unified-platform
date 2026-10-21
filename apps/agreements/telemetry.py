"""Logs, traces and error reporting for TFSC Agreements (§4.2), mirroring the CRM's.

- JSON lines on stdout (TFSC_LOG_FORMAT=json, the default in hosted mode) with request_id and trace ids; one access
  line per request with the route template only (no paths with ids, no query strings, no bodies).
- OpenTelemetry over OTLP/HTTP when OTEL_EXPORTER_OTLP_ENDPOINT is set (service tfsc-agreements); span URLs keep the
  path only (query strings carry sign-in codes and review tokens).
- Sentry when TFSC_SENTRY_DSN is set, without personal data.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
from datetime import UTC, datetime

request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar('tfsc_request_id', default=None)
ACCESS_FIELDS = ('method', 'route', 'status', 'duration_ms')
_state: dict[str, bool] = {}


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {'ts': datetime.fromtimestamp(record.created, UTC).isoformat(timespec='milliseconds'), 'level': record.levelname.lower(),
               'logger': record.name, 'msg': record.getMessage()}
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


def _scrub(event, hint):
    req = event.get('request') or {}
    for k in ('cookies', 'headers', 'data', 'query_string', 'env'):
        req.pop(k, None)
    event.pop('user', None)
    return event


QUERY_KEYS = ('http.url', 'url.full', 'http.target', 'url.query')


def drop_query_strings(span, scope=None):
    """As each span starts, and again as the FastAPI server hook (the ASGI middleware re-sets the URL after start)."""
    attrs = getattr(span, 'attributes', None) or {}
    for k in QUERY_KEYS:
        v = attrs.get(k)
        if isinstance(v, str) and (k == 'url.query' or '?' in v):
            span.set_attribute(k, '' if k == 'url.query' else v.split('?', 1)[0])


def instrument_engine(engine):
    """A span per SQL statement (text only, never parameters) from SQLAlchemy's cursor events."""
    if _state.get('tracing') and not getattr(engine, '_tfsc_traced', False):
        from opentelemetry import trace
        from opentelemetry.trace import SpanKind, Status, StatusCode
        from sqlalchemy import event
        tracer = trace.get_tracer('tfsc.sql')

        @event.listens_for(engine, 'before_cursor_execute')
        def _start(conn, cursor, statement, parameters, context, executemany):
            context._tfsc_span = tracer.start_span((statement.split(None, 1) or ['SQL'])[0].upper(), kind=SpanKind.CLIENT,
                                                   attributes={'db.system': engine.dialect.name, 'db.statement': statement[:2000]})

        @event.listens_for(engine, 'after_cursor_execute')
        def _end(conn, cursor, statement, parameters, context, executemany):
            span = getattr(context, '_tfsc_span', None)
            if span is not None:
                span.end()

        @event.listens_for(engine, 'handle_error')
        def _error(ctx):
            span = getattr(ctx.execution_context, '_tfsc_span', None) if ctx.execution_context is not None else None
            if span is not None:
                span.set_status(Status(StatusCode.ERROR, type(ctx.original_exception).__name__))
                span.end()
        engine._tfsc_traced = True


def configure(service, version, hosted, engine=None, app=None):
    if _state.get('configured'):
        return
    if os.getenv('TFSC_LOG_FORMAT', 'json' if hosted else 'text') == 'json':
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root = logging.getLogger()
        root.handlers[:] = [handler]
        root.setLevel(os.getenv('TFSC_LOG_LEVEL', 'INFO').upper())
        for name in ('uvicorn', 'uvicorn.error'):
            logging.getLogger(name).handlers[:] = []
            logging.getLogger(name).propagate = True
        logging.getLogger('uvicorn.access').handlers[:] = []  # raw paths and client IPs; tfsc.access replaces it
        logging.getLogger('uvicorn.access').propagate = False
    logging.getLogger('httpx').setLevel(logging.WARNING)
    if os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT') or os.getenv('OTEL_EXPORTER_OTLP_TRACES_ENDPOINT'):
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        class DropQueryStrings(SpanProcessor):
            def on_start(self, span, parent_context=None):
                drop_query_strings(span)

        provider = TracerProvider(resource=Resource.create({'service.name': os.getenv('OTEL_SERVICE_NAME', service), 'service.version': version}))
        provider.add_span_processor(DropQueryStrings())
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        HTTPXClientInstrumentor().instrument()
        _state['tracing'] = True
        if engine is not None:
            instrument_engine(engine)
        if app is not None:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument_app(app, excluded_urls='/api/health', server_request_hook=drop_query_strings)
    if os.getenv('TFSC_SENTRY_DSN'):
        import sentry_sdk
        sentry_sdk.init(dsn=os.environ['TFSC_SENTRY_DSN'], release=f'{service}@{version}', send_default_pii=False,
                        max_request_body_size='never', traces_sample_rate=0.0, before_send=_scrub)
    _state['configured'] = True

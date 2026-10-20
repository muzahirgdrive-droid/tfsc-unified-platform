"""TFSC Sales CRM v2 — FastAPI application factory.

Run: uvicorn tfsc_crm.main:app  (CRM_DATABASE_URL must point at a migrated PostgreSQL 16 database)
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.exc import IntegrityError

from . import observability
from .config import get_settings
from .domain.activities import RowRejected

VERSION = '2.0.0-dev'
log = logging.getLogger('tfsc_crm')

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob:; connect-src 'self'; "
       "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
MAX_BODY = 32_000_000


def create_app() -> FastAPI:
    s = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from .domain import bootstrap
        bootstrap.ensure_setup_key()
        stops = []
        if os.getenv('CRM_INLINE_DISPATCHER', '1' if not s.hosted else '0') == '1':
            from .workers import dispatcher
            stops.append(dispatcher.start_background())
        if os.getenv('CRM_INLINE_SCHEDULER', '1' if s.mode == 'local' else '0') == '1':
            from .workers import scheduler
            stops.append(scheduler.start_background())
        yield
        for stop in stops:
            stop()
        from .db.session import dispose
        dispose()

    app = FastAPI(title='TFSC Sales CRM', version=VERSION, lifespan=lifespan,
                  docs_url='/api/docs' if not s.hosted else None, redoc_url=None, openapi_url='/api/openapi.json')

    allowed_hosts = {urlsplit(s.origin).netloc}
    if not s.hosted:
        allowed_hosts |= {'testserver', 'localhost', '127.0.0.1', f'localhost:{urlsplit(s.origin).port or 80}', f'127.0.0.1:{urlsplit(s.origin).port or 80}'}

    @app.middleware('http')
    async def boundary(request: Request, call_next):
        started = time.perf_counter()
        rid = request.headers.get('x-request-id') or uuid.uuid4().hex
        # /metrics is scraped by pod/service address and guarded by its bearer token instead of the Host check.
        if request.headers.get('host', '') not in allowed_hosts and request.url.path != '/metrics':
            return JSONResponse({'error': 'Invalid application host.'}, 403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if (origin is not None and origin != s.origin and not (not s.hosted and urlsplit(origin).netloc in allowed_hosts)) \
                    or request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'error': 'Request origin was rejected.'}, 403)
            length = request.headers.get('content-length')
            if length and length.isdigit() and int(length) > MAX_BODY:
                return JSONResponse({'error': 'Request exceeds the 32 MB limit.'}, 413)
        try:
            response = await call_next(request)
        except Exception:
            log.exception('request failed rid=%s path=%s', rid, request.url.path)
            response = JSONResponse({'error': 'The request could not be completed. Please retry.', 'request_id': rid}, 500)
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
            'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': CSP, 'X-Request-Id': rid,
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
            'Server-Timing': f'app;dur={(time.perf_counter() - started) * 1000:.1f}',
        })
        if s.hosted:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        route = request.scope.get('route')
        observability.observe(request.method, getattr(route, 'path', 'unmatched'), response.status_code, time.perf_counter() - started)
        return response

    @app.get('/metrics', include_in_schema=False)
    def metrics(request: Request):
        import secrets as _secrets

        from fastapi.responses import PlainTextResponse
        token = get_settings().metrics_token
        auth = request.headers.get('authorization', '')
        if not token or not _secrets.compare_digest(auth, f'Bearer {token}'):
            return JSONResponse({'error': 'Not found.'}, 404)
        return PlainTextResponse(observability.render(observability.gauges()), media_type='text/plain; version=0.0.4')

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, e: HTTPException):
        return JSONResponse({'error': e.detail}, e.status_code, headers=getattr(e, 'headers', None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, e: RequestValidationError):
        details = [{'loc': [str(x) for x in err.get('loc', [])], 'message': err.get('msg', '')} for err in e.errors()]
        return JSONResponse({'error': details[0]['message'] if details else 'Invalid request.', 'details': details}, 422)

    @app.exception_handler(RowRejected)
    async def row_rejected(request: Request, e: RowRejected):
        return JSONResponse({'error': e.issues[0]['message'] if e.issues else 'Row rejected.', 'issues': e.issues, 'kind': e.kind}, 400)

    @app.exception_handler(IntegrityError)
    async def integrity(request: Request, e: IntegrityError):
        return JSONResponse({'error': 'This change conflicts with an existing record. Refresh and review it.'}, 409)

    from .api import routes_activities, routes_auth, routes_org, routes_pipeline, routes_settings
    for r in (routes_auth, routes_org, routes_settings, routes_activities, routes_pipeline):
        app.include_router(r.router)
    for optional in ('routes_dashboard', 'routes_meetings', 'routes_events', 'routes_imports', 'routes_file_imports', 'routes_agreements',
                     'routes_security'):
        try:
            mod = __import__(f'{__package__}.api.{optional}', fromlist=['router'])
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.endswith(optional):
                continue
            raise
        app.include_router(mod.router)

    web = Path(s.web_dist) if s.web_dist else Path(__file__).resolve().parents[2] / 'crm-web' / 'dist'

    @app.get('/{path:path}', include_in_schema=False)
    def spa(path: str):
        if path.startswith('api/'):
            raise HTTPException(404, 'Not found.')
        target = (web / path).resolve()
        if path and target.is_file() and target.is_relative_to(web.resolve()):
            return FileResponse(target)
        index = web / 'index.html'
        if not index.exists():
            return JSONResponse({'app': 'TFSC Sales CRM', 'status': 'Frontend build required: npm run build in apps/crm-web.'}, 503)
        return FileResponse(index)

    return app


app = create_app()

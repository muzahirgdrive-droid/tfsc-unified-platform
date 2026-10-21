"""Prometheus metrics (§23 system health, §24 proof). Plain text exposition, no extra dependency.

Request counts and latency histograms by route template (never raw paths, so no ids or PII in labels). Several
uvicorn workers in one container share their counters through CRM_METRICS_DIR: each process writes a snapshot every
few seconds and the process that answers a scrape adds them all up, so a scrape never lands on one worker's partial
numbers. At scrape time: outbox depth, live SSE connections, DB pool usage. `GET /metrics` needs
`Authorization: Bearer <CRM_METRICS_TOKEN>`; without a configured token the endpoint does not exist.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from pathlib import Path

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.5, 5.0, 10.0)
_lock = threading.Lock()
_count: dict[tuple[str, str, str], int] = defaultdict(int)
_hist: dict[tuple[str, str], list[int]] = {}
_sum: dict[tuple[str, str], float] = defaultdict(float)
_flusher: list[threading.Thread] = []
FLUSH_S = 5.0


def _dir() -> Path | None:
    d = os.getenv('CRM_METRICS_DIR')
    return Path(d) if d else None


def _sse() -> int:
    try:
        from .realtime import broker
        return int(broker().connections())
    except Exception:
        return 0


def _snapshot() -> dict:
    sse = _sse()
    with _lock:
        return {'count': [[*k, n] for k, n in _count.items()], 'hist': [[*k, h] for k, h in _hist.items()],
                'sum': [[*k, v] for k, v in _sum.items()], 'sse': sse, 'at': time.time()}


def flush() -> None:
    d = _dir()
    if d is None:
        return
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f'.{os.getpid()}.tmp'
    tmp.write_text(json.dumps(_snapshot()))
    os.replace(tmp, d / f'{os.getpid()}.json')


def _start_flusher() -> None:
    def loop() -> None:
        while True:
            time.sleep(FLUSH_S)
            try:
                flush()
            except OSError:
                pass
    t = threading.Thread(target=loop, name='metrics-flush', daemon=True)
    t.start()
    _flusher.append(t)


def _merged() -> tuple[dict, dict, dict, int]:
    """This process's numbers, plus every sibling's last snapshot when CRM_METRICS_DIR is shared (counters from a worker
    that has exited stay counted, so totals never go backwards; its SSE connections stop counting after 30 s)."""
    d = _dir()
    if d is None:
        with _lock:
            return dict(_count), {k: list(v) for k, v in _hist.items()}, dict(_sum), _sse()
    flush()
    count: dict = defaultdict(int)
    hist: dict = {}
    total: dict = defaultdict(float)
    sse = 0
    for f in d.glob('*.json'):
        try:
            snap = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        for m, r, st, n in snap['count']:
            count[(m, r, st)] += n
        for m, r, h in snap['hist']:
            acc = hist.setdefault((m, r), [0] * len(h))
            for i, n in enumerate(h):
                acc[i] += n
        for m, r, v in snap['sum']:
            total[(m, r)] += v
        if snap.get('at', 0) > time.time() - 30:
            sse += snap.get('sse', 0)
    return count, hist, total, sse


def observe(method: str, route: str, status: int, seconds: float) -> None:
    key = (method, route)
    if not _flusher and _dir() is not None:
        _start_flusher()
    with _lock:
        _count[(method, route, str(status))] += 1
        h = _hist.setdefault(key, [0] * (len(BUCKETS) + 1))
        for i, b in enumerate(BUCKETS):
            if seconds <= b:
                h[i] += 1
        h[-1] += 1
        _sum[key] += seconds


def _esc(v: str) -> str:
    return v.replace('\\', '\\\\').replace('"', '\\"')


def render(extra: dict[str, float]) -> str:
    out = ['# HELP crm_http_requests_total Requests by method, route template and status.', '# TYPE crm_http_requests_total counter']
    count, hist, total, sse = _merged()
    extra = {**extra, 'crm_sse_connections': float(sse)}
    for (m, r, s), n in sorted(count.items()):
        out.append(f'crm_http_requests_total{{method="{m}",route="{_esc(r)}",status="{s}"}} {n}')
    out += ['# HELP crm_http_request_duration_seconds Request latency.', '# TYPE crm_http_request_duration_seconds histogram']
    for (m, r), h in sorted(hist.items()):
        lbl = f'method="{m}",route="{_esc(r)}"'
        for b, n in zip(BUCKETS, h, strict=False):
            out.append(f'crm_http_request_duration_seconds_bucket{{{lbl},le="{b}"}} {n}')
        out.append(f'crm_http_request_duration_seconds_bucket{{{lbl},le="+Inf"}} {h[-1]}')
        out.append(f'crm_http_request_duration_seconds_sum{{{lbl}}} {total[(m, r)]:.6f}')
        out.append(f'crm_http_request_duration_seconds_count{{{lbl}}} {h[-1]}')
    for name, value in sorted(extra.items()):
        out += [f'# TYPE {name} gauge', f'{name} {value}']
    return '\n'.join(out) + '\n'


def gauges() -> dict[str, float]:
    from sqlalchemy import func, select

    from .db import models as m
    from .db.session import engine, system_tx
    g: dict[str, float] = {}
    try:
        with system_tx() as c:
            for state, n in c.execute(select(m.outbox.c.state, func.count()).where(m.outbox.c.state.in_(('pending', 'dead'))).group_by(m.outbox.c.state)):
                g[f'crm_outbox_{state}'] = n
        g.setdefault('crm_outbox_pending', 0)
        g.setdefault('crm_outbox_dead', 0)
        pool = engine().pool
        g['crm_db_pool_checked_out'] = float(getattr(pool, 'checkedout', lambda: 0)())
        g['crm_db_up'] = 1
    except Exception:
        g['crm_db_up'] = 0
    return g

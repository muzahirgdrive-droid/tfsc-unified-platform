"""Prometheus metrics (§23 system health, §24 proof). Plain text exposition, no extra dependency.

Per process: request counts and latency histograms by route template (never raw paths, so no ids or PII in labels).
At scrape time: outbox depth, live SSE connections, DB pool usage. `GET /metrics` needs `Authorization: Bearer
<CRM_METRICS_TOKEN>`; without a configured token the endpoint does not exist.
"""
from __future__ import annotations

import threading
from collections import defaultdict

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.5, 5.0, 10.0)
_lock = threading.Lock()
_count: dict[tuple[str, str, str], int] = defaultdict(int)
_hist: dict[tuple[str, str], list[int]] = {}
_sum: dict[tuple[str, str], float] = defaultdict(float)


def observe(method: str, route: str, status: int, seconds: float) -> None:
    key = (method, route)
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
    with _lock:
        for (m, r, s), n in sorted(_count.items()):
            out.append(f'crm_http_requests_total{{method="{m}",route="{_esc(r)}",status="{s}"}} {n}')
        out += ['# HELP crm_http_request_duration_seconds Request latency.', '# TYPE crm_http_request_duration_seconds histogram']
        for (m, r), h in sorted(_hist.items()):
            lbl = f'method="{m}",route="{_esc(r)}"'
            for b, n in zip(BUCKETS, h, strict=False):
                out.append(f'crm_http_request_duration_seconds_bucket{{{lbl},le="{b}"}} {n}')
            out.append(f'crm_http_request_duration_seconds_bucket{{{lbl},le="+Inf"}} {h[-1]}')
            out.append(f'crm_http_request_duration_seconds_sum{{{lbl}}} {_sum[(m, r)]:.6f}')
            out.append(f'crm_http_request_duration_seconds_count{{{lbl}}} {h[-1]}')
    for name, value in sorted(extra.items()):
        out += [f'# TYPE {name} gauge', f'{name} {value}']
    return '\n'.join(out) + '\n'


def gauges() -> dict[str, float]:
    from sqlalchemy import func, select

    from .db import models as m
    from .db.session import engine, system_tx
    from .realtime import broker
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
    try:
        g['crm_sse_connections'] = float(broker().connections())
    except Exception:
        g['crm_sse_connections'] = 0
    return g

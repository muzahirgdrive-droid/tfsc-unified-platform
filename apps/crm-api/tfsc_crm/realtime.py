"""Realtime fan-out for SSE (ADR-0009).

Redis pub/sub when CRM_REDIS_URL is set (required for >1 API process); otherwise an
in-process broker (single process / tests). Events are published per org and are
already role-filtered by `audience` before they reach a browser.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from .config import get_settings

log = logging.getLogger('tfsc_crm.realtime')

# audience: 'all' (every signed-in user of the org) | 'management' (Supervisor+) | 'user:<uuid>'
AUDIENCE_BY_TOPIC = {
    'sale.celebrated': 'all',
    'dashboard.changed': 'all',
    'flag.raised': 'management',
    'meeting.changed': 'management',
}


class _LocalBroker:
    def __init__(self) -> None:
        self._subs: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = defaultdict(set)
        self._lock = threading.Lock()

    def publish(self, org_id: str, event: dict[str, Any]) -> int:
        with self._lock:
            subs = list(self._subs.get(org_id, ()))
        for loop, q in subs:
            loop.call_soon_threadsafe(q.put_nowait, event)
        return len(subs)

    async def subscribe(self, org_id: str) -> AsyncIterator[dict[str, Any]]:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        entry = (asyncio.get_running_loop(), q)
        with self._lock:
            self._subs[org_id].add(entry)
        try:
            while True:
                yield await q.get()
        finally:
            with self._lock:
                self._subs[org_id].discard(entry)

    def connections(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._subs.values())


class _RedisBroker:
    def __init__(self, url: str) -> None:
        import redis
        import redis.asyncio as aioredis
        self._sync = redis.Redis.from_url(url)
        self._url = url
        self._aio = aioredis
        self._count = 0

    def publish(self, org_id: str, event: dict[str, Any]) -> int:
        payload = json.dumps(event, default=str)
        self._sync.xadd(f'crm:events:{org_id}', {'e': payload}, maxlen=5000, approximate=True)  # short replay buffer
        return int(self._sync.publish(f'crm:org:{org_id}', payload))

    async def subscribe(self, org_id: str) -> AsyncIterator[dict[str, Any]]:
        r = self._aio.Redis.from_url(self._url)
        ps = r.pubsub()
        await ps.subscribe(f'crm:org:{org_id}')
        self._count += 1
        try:
            async for msg in ps.listen():
                if msg.get('type') == 'message':
                    yield json.loads(msg['data'])
        finally:
            self._count -= 1
            await ps.unsubscribe()
            await r.aclose()

    def connections(self) -> int:
        return self._count


_broker: _LocalBroker | _RedisBroker | None = None


def broker() -> _LocalBroker | _RedisBroker:
    global _broker
    if _broker is None:
        url = get_settings().redis_url
        _broker = _RedisBroker(url) if url else _LocalBroker()
    return _broker


def visible_to(event: dict[str, Any], user_id: str, rank: int) -> bool:
    aud = event.get('audience', 'all')
    if aud == 'all':
        return True
    if aud == 'management':
        return rank >= 1
    if aud.startswith('user:'):
        return aud[5:] == user_id
    return False

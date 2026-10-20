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
from collections.abc import AsyncGenerator
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


def _put(q: asyncio.Queue, event: dict[str, Any]) -> None:
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:  # a stalled browser drops events rather than growing memory; it re-syncs on reconnect
        log.warning('sse subscriber queue full; dropping event %s', event.get('id'))


class _LocalBroker:
    def __init__(self) -> None:
        self._subs: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = defaultdict(set)
        self._lock = threading.Lock()

    def publish(self, org_id: str, event: dict[str, Any]) -> int:
        with self._lock:
            subs = list(self._subs.get(org_id, ()))
        try:
            here: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        for loop, q in subs:
            if loop is here:
                _put(q, event)  # same loop (the Redis listener): no cross-thread wake-up per subscriber
            else:
                loop.call_soon_threadsafe(_put, q, event)
        return len(subs)

    async def subscribe(self, org_id: str) -> AsyncGenerator[dict[str, Any], None]:
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
    """Cross-process fan-out. Publishers write to Redis; each worker process holds ONE subscription per org and fans
    events out to its own SSE connections in memory (so 10,000 browsers do not mean 10,000 Redis connections)."""

    def __init__(self, url: str) -> None:
        import redis
        import redis.asyncio as aioredis
        self._sync = redis.Redis.from_url(url)
        self._url = url
        self._aio = aioredis
        self._local = _LocalBroker()
        self._listeners: dict[tuple[int, str], asyncio.Task] = {}

    def publish(self, org_id: str, event: dict[str, Any]) -> int:
        payload = json.dumps(event, default=str)
        self._sync.xadd(f'crm:events:{org_id}', {'e': payload}, maxlen=5000, approximate=True)  # short replay buffer
        return int(self._sync.publish(f'crm:org:{org_id}', payload))

    async def _listen(self, org_id: str) -> None:
        delay = 0.5
        while True:
            r = self._aio.Redis.from_url(self._url)
            try:
                ps = r.pubsub()
                await ps.subscribe(f'crm:org:{org_id}')
                delay = 0.5
                async for msg in ps.listen():
                    if msg.get('type') == 'message':
                        self._local.publish(org_id, json.loads(msg['data']))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning('redis subscription lost; reconnecting', exc_info=True)
                await asyncio.sleep(delay)
                delay = min(10.0, delay * 2)
            finally:
                await r.aclose()

    async def subscribe(self, org_id: str) -> AsyncGenerator[dict[str, Any], None]:
        key = (id(asyncio.get_running_loop()), org_id)
        task = self._listeners.get(key)
        if task is None or task.done():
            self._listeners[key] = asyncio.create_task(self._listen(org_id), name=f'redis-listener-{org_id}')
        local = self._local.subscribe(org_id)
        try:
            async for event in local:
                yield event
        finally:
            await local.aclose()  # `async for` does not close the inner generator when this one is closed

    def connections(self) -> int:
        return self._local.connections()


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

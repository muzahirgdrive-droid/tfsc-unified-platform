"""Sliding-window rate limits (Redis when configured; in-process fallback for local/test)."""
from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import HTTPException

from ..config import get_settings

_local: dict[str, deque] = {}
_lock = threading.Lock()
_redis = None


def _client():
    global _redis
    url = get_settings().redis_url
    if not url:
        return None
    if _redis is None:
        import redis
        _redis = redis.Redis.from_url(url, socket_timeout=0.5)
    return _redis


def hit(key: str, limit: int, window_s: int) -> bool:
    """Record one hit; return True if within the limit."""
    now = time.time()
    r = _client()
    if r is not None:
        try:
            k = f'rl:{key}'
            p = r.pipeline()
            p.zremrangebyscore(k, 0, now - window_s)
            p.zadd(k, {f'{now}:{id(p)}': now})
            p.zcard(k)
            p.expire(k, window_s)
            count = p.execute()[2]
            return count <= limit
        except Exception:  # Redis outage must not lock everyone out; fall back to the local window
            import logging
            logging.getLogger('tfsc_crm').warning('rate limiter: Redis unavailable, using local window')
    with _lock:
        q = _local.setdefault(key, deque())
        while q and q[0] < now - window_s:
            q.popleft()
        q.append(now)
        return len(q) <= limit


def peek(key: str, window_s: int) -> int:
    """Current number of hits in the window, without recording one."""
    now = time.time()
    r = _client()
    if r is not None:
        try:
            k = f'rl:{key}'
            r.zremrangebyscore(k, 0, now - window_s)
            return int(r.zcard(k))
        except Exception:  # Redis outage: fall back to the local window (same policy as hit())
            import logging
            logging.getLogger('tfsc_crm').warning('rate limiter: Redis unavailable, using local window')
    with _lock:
        q = _local.get(key)
        return sum(1 for t in q if t >= now - window_s) if q else 0


def enforce(key: str, limit: int, window_s: int, message: str = 'Too many attempts. Please wait and try again.') -> None:
    if not hit(key, limit, window_s):
        raise HTTPException(429, message)


def reset_local() -> None:
    with _lock:
        _local.clear()

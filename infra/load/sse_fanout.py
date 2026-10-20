"""SSE fan-out test (§24: celebration < 3 s to all clients; live connections).

Opens N concurrent SSE connections (/api/events) as signed-in synthetic users, then publishes one sale.celebrated
event through the normal outbox -> dispatcher -> Redis -> worker path and measures, for every connection, the time
from the outbox commit to the event's arrival. Clients are raw sockets spread over several processes so the
harness is not what gets measured. Synthetic users from infra/load/users.json; no client data.

Usage: CRM_DATABASE_URL=... CRM_FIELD_KEY=... python infra/load/sse_fanout.py http://127.0.0.1:8766 3000 [processes]
"""
from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import statistics
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'apps' / 'crm-api'))

OPEN_AT_ONCE = 100  # per process: browsers do not all connect in the same millisecond


def _client(base: str, cookie: str, tokens: list[str], marker: str, out: mp.Queue, go: mp.Event) -> None:  # type: ignore[valid-type]
    u = urlsplit(base)
    host, port = u.hostname or '127.0.0.1', u.port or 80
    needle = marker.encode()

    async def run() -> None:
        errors: dict[str, int] = {}
        arrivals: list[float] = []
        opened = 0
        gate = asyncio.Semaphore(OPEN_AT_ONCE)
        all_open = asyncio.Event()

        async def one(token: str) -> None:
            nonlocal opened
            try:
                async with gate:
                    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 30)
                    writer.write(f'GET /api/events HTTP/1.1\r\nHost: {u.netloc}\r\nCookie: {cookie}={token}\r\n'
                                 'Accept: text/event-stream\r\n\r\n'.encode())
                    head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 60)
                status = int(head.split(b' ', 2)[1])
                if status != 200:
                    raise RuntimeError(f'HTTP {status}')
                opened += 1
                if opened == len(tokens):
                    all_open.set()
                tail = b''
                while True:
                    data = await reader.read(65536)
                    if not data:
                        raise ConnectionError('closed by server')
                    if needle in tail + data:
                        arrivals.append(time.time())
                        break
                    tail = data[-len(needle):]
                writer.close()
            except Exception as e:  # counted and reported
                kind = type(e).__name__ + (f': {e}' if isinstance(e, RuntimeError) else '')
                errors[kind] = errors.get(kind, 0) + 1
                if opened + sum(errors.values()) >= len(tokens):
                    all_open.set()

        t0 = time.time()
        tasks = [asyncio.create_task(one(t)) for t in tokens]
        try:
            await asyncio.wait_for(all_open.wait(), 600)
        except TimeoutError:
            pass
        out.put(('open', opened, time.time() - t0, dict(errors)))
        await asyncio.get_running_loop().run_in_executor(None, go.wait, 900)
        await asyncio.wait(tasks, timeout=60)
        out.put(('done', arrivals, dict(errors)))
        for t in tasks:
            t.cancel()
    asyncio.run(run())


def main(base: str, n: int, procs: int) -> None:
    import secrets
    from datetime import timedelta

    from sqlalchemy import select

    from tfsc_crm.db import models as m
    from tfsc_crm.db.session import system_tx
    from tfsc_crm.domain import outbox
    from tfsc_crm.pacific import utcnow
    from tfsc_crm.security.auth import COOKIE, digest
    users = json.loads((Path(__file__).resolve().parent / 'users.json').read_text())
    accounts = users['agents'] + users['supervisors'] + users['managers']
    # Sessions are minted directly: sign-in is not what this test measures (and one address may sign in 1,000 times / 15 min).
    now = utcnow()
    tokens: list[str] = []
    with system_tx() as c:
        ids = dict(c.execute(select(m.users.c.email, m.users.c.id).where(m.users.c.email.in_(accounts))).all())
        org = c.execute(select(m.orgs.c.id).limit(1)).scalar_one()
        rows = []
        for i in range(n):
            tokens.append(secrets.token_urlsafe(32))
            rows.append({'token_hash': digest(tokens[-1]), 'user_id': ids[accounts[i % len(accounts)]], 'csrf': secrets.token_urlsafe(16),
                         'created_at': now, 'last_seen_at': now, 'expires_at': now + timedelta(hours=2), 'idle_expires_at': now + timedelta(hours=1)})
        c.execute(m.user_sessions.insert(), rows)
    marker = f'fanout-{uuid.uuid4().hex[:8]}'
    ctx = mp.get_context('spawn')
    out: mp.Queue = ctx.Queue()
    go = ctx.Event()
    workers = [ctx.Process(target=_client, args=(base, COOKIE, tokens[k::procs], marker, out, go), daemon=True) for k in range(procs)]
    for w in workers:
        w.start()
    opened, slowest, errors = 0, 0.0, {}
    for _ in workers:
        _, k, secs, errs = out.get()
        opened, slowest = opened + k, max(slowest, secs)
        for e, v in errs.items():
            errors[e] = errors.get(e, 0) + v
    print(f'streams open: {opened}/{n} in {slowest:.1f} s ({procs} client processes); errors: {errors}', flush=True)
    time.sleep(2)
    with system_tx() as c:
        outbox.emit(c, org, 'sale.celebrated', {'celebration_id': marker, 'agent_id': str(uuid.uuid4()), 'agent_name': 'Load Agent',
                                                'supervisor_name': 'Load Supervisor', 'message': f'Load test {marker}'})
    sent_at = time.time()
    go.set()
    got: list[float] = []
    errors = {}  # the final report from each process includes the errors it had while opening
    for _ in workers:
        _, arrivals, errs = out.get()
        got += [a - sent_at for a in arrivals]
        for e, v in errs.items():
            errors[e] = errors.get(e, 0) + v
    for w in workers:
        w.join(10)
    got.sort()
    if got:
        print(json.dumps({'connections': opened, 'received': len(got), 'errors': errors, 'p50_s': round(statistics.median(got), 3),
                          'p95_s': round(got[int(0.95 * (len(got) - 1))], 3), 'max_s': round(got[-1], 3)}), flush=True)
    else:
        print(f'no deliveries; errors: {errors}', flush=True)


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 2)

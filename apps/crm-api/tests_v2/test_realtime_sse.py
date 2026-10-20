"""SSE plumbing under churn (§24): clients leaving mid-wait unsubscribe cleanly; one Redis subscription per process."""
import asyncio
import os
import uuid

import pytest


def test_client_leaving_while_waiting_for_an_event_unsubscribes_cleanly():
    from tfsc_crm.api.routes_events import relay
    from tfsc_crm.realtime import _LocalBroker
    b = _LocalBroker()

    async def run():
        async def connected() -> bool:
            return False
        body = relay(b.subscribe('org-x'), connected, 'u1', 0, [])
        assert await body.__anext__() == b'retry: 3000\n\n'
        waiting = asyncio.ensure_future(body.__anext__())  # parked until an event or a keep-alive
        await asyncio.sleep(0.05)
        assert b.connections() == 1
        waiting.cancel()  # what Starlette does to the response when the browser goes away
        with pytest.raises(asyncio.CancelledError):  # not "aclose(): asynchronous generator is already running"
            await waiting
        await body.aclose()
        await asyncio.sleep(0)
        assert b.connections() == 0
    asyncio.run(run())


def test_disconnect_noticed_between_events_closes_the_subscription():
    from tfsc_crm.api.routes_events import relay
    from tfsc_crm.realtime import _LocalBroker
    b = _LocalBroker()

    async def run():
        gone = False

        async def disconnected() -> bool:
            return gone
        body = relay(b.subscribe('org-y'), disconnected, 'u1', 0, [{'id': 'c-1', 'topic': 'sale.celebrated'}])
        assert await body.__anext__() == b'retry: 3000\n\n'
        assert b'sale.celebrated' in await body.__anext__()  # backlog
        nxt = asyncio.ensure_future(body.__anext__())
        await asyncio.sleep(0.05)
        b.publish('org-y', {'id': 'e-1', 'topic': 'flag.raised', 'audience': 'management'})  # not for an agent: skipped
        b.publish('org-y', {'id': 'e-2', 'topic': 'dashboard.changed', 'audience': 'all'})
        assert b'e-2' in await asyncio.wait_for(nxt, 5)
        gone = True
        with pytest.raises(StopAsyncIteration):
            await body.__anext__()
        assert b.connections() == 0
    asyncio.run(run())


def test_redis_broker_uses_one_subscription_per_process_for_many_clients():
    url = os.getenv('CRM_TEST_REDIS_URL')
    if not url:
        pytest.skip('CRM_TEST_REDIS_URL not set')
    from tfsc_crm.realtime import _RedisBroker
    b = _RedisBroker(url)
    org = f'test-{uuid.uuid4().hex}'
    channel = f'crm:org:{org}'

    async def run():
        subs = [b.subscribe(org).__aiter__() for _ in range(50)]
        waits = [asyncio.ensure_future(s.__anext__()) for s in subs]
        for _ in range(100):
            if b.connections() == 50 and b._sync.pubsub_numsub(channel)[0][1] >= 1:
                break
            await asyncio.sleep(0.05)
        assert b.connections() == 50
        assert b._sync.pubsub_numsub(channel)[0][1] == 1  # 50 browsers, one Redis subscription
        assert b.publish(org, {'id': 'r-1', 'topic': 'dashboard.changed', 'audience': 'all'}) == 1
        got = await asyncio.wait_for(asyncio.gather(*waits), 5)
        assert [g['id'] for g in got] == ['r-1'] * 50
        for s in subs:
            await s.aclose()
        assert b.connections() == 0
    asyncio.run(run())
    b._sync.delete(f'crm:events:{org}')

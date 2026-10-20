"""Server-Sent Events stream and Wall of Fame (§18)."""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from ..db import models as m
from ..db.session import read
from ..pacific import PACIFIC, business_date, utcnow
from ..realtime import broker, visible_to
from ..security.auth import load_actor
from .deps import ActorDep

router = APIRouter(prefix='/api', tags=['realtime'])
HEARTBEAT_S = float(__import__('os').getenv('CRM_SSE_HEARTBEAT_S', '15'))


def _sse(event: dict) -> bytes:
    eid = event.get('id', '')
    return f'id: {eid}\nevent: {event.get("topic", "message")}\ndata: {json.dumps(event, default=str)}\n\n'.encode()


@router.get('/events', include_in_schema=False)
async def events(request: Request) -> StreamingResponse:
    a = load_actor(request)
    org, uid, rank = str(a.org_id), str(a.id), a.rank
    # Anyone who signs in within 10 minutes of a sale sees the celebration once (client de-duplicates by id).
    with read(a.org_id, a.id) as c:
        recent = c.execute(select(m.celebrations).where(m.celebrations.c.created_at > utcnow() - timedelta(minutes=10))
                           .order_by(m.celebrations.c.created_at)).mappings().all()
    backlog = [{'id': f'c-{r["id"]}', 'topic': 'sale.celebrated', 'celebration_id': str(r['id']), 'agent_id': str(r['agent_id']),
                'message': r['message'], 'audience': 'all', 'at': r['created_at'].isoformat()} for r in recent]

    async def stream():
        yield b'retry: 3000\n\n'
        for e in backlog:
            yield _sse(e)
        sub = broker().subscribe(org).__aiter__()
        pending: asyncio.Task | None = None
        try:
            while True:
                if await request.is_disconnected():
                    break
                if pending is None:
                    pending = asyncio.ensure_future(sub.__anext__())
                done, _ = await asyncio.wait({pending}, timeout=HEARTBEAT_S)
                if not done:
                    yield b': keep-alive\n\n'
                    continue
                event = pending.result()
                pending = None
                if visible_to(event, uid, rank):
                    yield _sse(event)
        finally:
            if pending is not None:
                pending.cancel()
            await sub.aclose()  # type: ignore[attr-defined]

    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@router.get('/celebrations')
def wall_of_fame(a: ActorDep, months: int = 6) -> dict:
    """Recent sales (agent, supervisor, date) and monthly champions. No client details (§18)."""
    cel = m.celebrations
    agent, sup = m.users.alias('agent'), m.users.alias('sup')
    since = utcnow() - timedelta(days=31 * max(1, min(months, 24)))
    with read(a.org_id, a.id) as c:
        rows = c.execute(select(cel.c.id, cel.c.created_at, cel.c.agent_id, agent.c.display_name.label('agent'), sup.c.display_name.label('supervisor'),
                                agent.c.photo_object_key)
                         .join(agent, agent.c.id == cel.c.agent_id).outerjoin(sup, sup.c.id == cel.c.supervisor_id)
                         .where(cel.c.created_at > since).order_by(cel.c.created_at.desc()).limit(200)).mappings().all()
        month = func.to_char(func.timezone('America/Los_Angeles', cel.c.created_at), 'YYYY-MM')
        champs = c.execute(select(month.label('month'), cel.c.agent_id, agent.c.display_name, func.count().label('sales'))
                           .join(agent, agent.c.id == cel.c.agent_id).where(cel.c.created_at > since)
                           .group_by(month, cel.c.agent_id, agent.c.display_name).order_by(month.desc(), func.count().desc())).mappings().all()
    best: dict[str, dict] = {}
    for r in champs:
        best.setdefault(r['month'], {'month': r['month'], 'agent_id': str(r['agent_id']), 'agent': r['display_name'], 'sales': r['sales']})
    return {'recent': [{'id': str(r['id']), 'date': r['created_at'].astimezone(PACIFIC).date().isoformat(), 'agent_id': str(r['agent_id']),
                        'agent': r['agent'], 'supervisor': r['supervisor'], 'has_photo': bool(r['photo_object_key'])} for r in rows],
            'champions': list(best.values()), 'business_date': business_date().isoformat()}

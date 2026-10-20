"""Read auditing and per-user read budgets (§21 anti-scraping).

Every list/detail read that exposes other people's prospects is logged in
data_access_log with the blind keys viewed. More than the configured number of
distinct prospects per hour (default 500) throttles the user (HTTP 429) and raises
one red `scraping_suspected` flag per hour for the Super Admin.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select, text

from ..db import models as m
from ..db.session import tx
from ..ids import uuid7
from ..security.roles import SUPER, Actor
from . import notify
from . import settings as cfg

DISTINCT_SQL = text("""
SELECT count(DISTINCT k) FROM data_access_log d, unnest(d.distinct_keys) k
WHERE d.actor_id = :a AND d.at > now() - interval '1 hour'
""")


def record_reads(actor: Actor, resource: str, keys: set[str]) -> None:
    if not keys or actor.rank >= SUPER:
        return
    with tx(actor.org_id, actor.id) as c:
        c.execute(m.data_access_log.insert().values(org_id=actor.org_id, actor_id=actor.id, resource=resource, count=len(keys), distinct_keys=sorted(keys)))
        limit = int(cfg.get(c, actor.org_id, 'anti_scraping').get('prospects_per_hour', 500))
        seen = c.execute(DISTINCT_SQL, {'a': actor.id}).scalar_one()
        if seen <= limit:
            return
        recent = c.execute(select(m.flags.c.id).where(m.flags.c.kind == 'scraping_suspected', m.flags.c.raised_for_user == actor.id,
                                                      m.flags.c.created_at > text("now() - interval '1 hour'"))).first()
        if not recent:
            fid = uuid7()
            c.execute(m.flags.insert().values(id=fid, org_id=actor.org_id, severity='red', kind='scraping_suspected', raised_for_user=actor.id,
                                              detail={'distinct_prospects_last_hour': seen, 'limit': limit, 'resource': resource}))
            notify.notify(c, actor.org_id, notify.super_admins(c, actor.org_id), 'flag.red',
                          {'flag_id': str(fid), 'kind': 'scraping_suspected', 'message': f'{actor.display_name} viewed {seen} prospects in the last hour.'})
    if seen > limit:
        raise HTTPException(429, 'You have viewed an unusual number of records. Access is paused; your supervisor has been notified.')

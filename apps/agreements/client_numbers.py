"""Client numbers: every client gets a permanent number, CL-0001 onward (CL-10000 after CL-9999).

A number is given once, when the client is created, and never changes or comes back, even if the client is renamed or
removed: the last number given is kept in settings ("client_numbers"), and the next one is one more than both that and the
highest number in use. Numbering is serialized: PostgreSQL takes a transaction-scoped advisory lock; on SQLite the caller's
transaction already holds the database's write lock (call these after the client row is written, or inside esign.write_tx()).

``ensure_client_numbers`` is the safety net: it numbers any client still without one (created by an older release running
after a rollback, or by code that writes the table directly), oldest first.
"""
from __future__ import annotations

import re

import database as db
from sqlalchemy import func, select, text, update

COUNTER = 'client_numbers'
LOCK = 714872002          # pg_advisory_xact_lock key (database.init_db uses 714872001)
PATTERN = re.compile(r'CL-(\d+)')


def format_number(n: int) -> str:
    return f'CL-{n:04d}'


def parse(value) -> int:
    m = PATTERN.fullmatch((value or '').strip().upper())
    return int(m.group(1)) if m else 0


def _lock(c) -> None:
    if c.dialect.name == 'postgresql':
        c.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': LOCK})


def _last(c) -> tuple[int, bool]:
    """The highest number given so far, and whether the counter row exists."""
    stored = c.execute(select(db.settings.c.value).where(db.settings.c.key == COUNTER)).scalar()
    last = stored['last'] if isinstance(stored, dict) and isinstance(stored.get('last'), int) else 0
    # Numbers are CL- and digits, so among the longest ones the text order is the number order (CL-10000 > CL-9999).
    longest = c.execute(select(func.max(func.length(db.clients.c.client_number)))).scalar()
    if longest:
        top = c.execute(select(func.max(db.clients.c.client_number)).where(func.length(db.clients.c.client_number) == longest)).scalar()
        last = max(last, parse(top))
    return last, stored is not None


def _save(c, last: int, exists: bool) -> None:
    if exists:
        c.execute(update(db.settings).where(db.settings.c.key == COUNTER).values(value={'last': last}))
    else:
        c.execute(db.settings.insert().values(key=COUNTER, value={'last': last}))


def assign(c, client_ids: list[str]) -> dict[str, str]:
    """Give the next numbers, in this order, to these clients (those that already have one keep it)."""
    if not client_ids:
        return {}
    _lock(c)
    have = dict(c.execute(select(db.clients.c.id, db.clients.c.client_number).where(db.clients.c.id.in_(client_ids))).all())
    last, exists = _last(c)
    out = {}
    for client_id in client_ids:
        if client_id not in have:
            continue
        if have[client_id]:
            out[client_id] = have[client_id]
            continue
        last += 1
        out[client_id] = format_number(last)
        c.execute(update(db.clients).where(db.clients.c.id == client_id, db.clients.c.client_number.is_(None)).values(client_number=out[client_id]))
        have[client_id] = out[client_id]
    _save(c, last, exists)
    return out


def assign_one(c, client_id: str) -> str:
    return assign(c, [client_id]).get(client_id, '')


def missing(c) -> bool:
    return c.execute(select(db.clients.c.id).where(db.clients.c.client_number.is_(None)).limit(1)).first() is not None


def ensure_client_numbers(c) -> int:
    """Number every client that has no number yet, oldest first. Returns how many were numbered."""
    if not missing(c):
        return 0
    _lock(c)
    pending = c.execute(select(db.clients.c.id).where(db.clients.c.client_number.is_(None))
                        .order_by(func.coalesce(db.clients.c.created, db.clients.c.updated), db.clients.c.updated, db.clients.c.id)).scalars().all()
    assign(c, list(pending))
    return len(pending)


def ensure_before_reading() -> None:
    """For read-only screens: number any client still without a number in a short write transaction of its own."""
    with db.engine.connect() as c:
        if not missing(c):
            return
    import esign
    with esign.write_tx() as c:
        ensure_client_numbers(c)


def search_terms(q: str) -> list[str]:
    """What a client-number search looks like in the stored form: 'cl-0012', 'CL12' or '12' all find CL-0012."""
    q = (q or '').strip().upper().replace(' ', '')
    m = re.fullmatch(r'(?:CL-?)?0*(\d{1,7})', q)
    return [format_number(int(m.group(1)))] if m and int(m.group(1)) else []

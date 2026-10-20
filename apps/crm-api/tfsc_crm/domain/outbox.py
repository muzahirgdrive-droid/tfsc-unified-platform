"""Transactional outbox (§19.3, ADR-0008). Events are written in the same transaction as
the business change and delivered at-least-once by the dispatcher worker."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Connection

from ..db import models as m
from ..ids import uuid7

SCHEMA_VERSION = 1


def emit(c: Connection, org_id: uuid.UUID, topic: str, payload: dict[str, Any], key: str | None = None) -> uuid.UUID:
    event_id = uuid7()
    c.execute(m.outbox.insert().values(event_id=event_id, org_id=org_id, topic=topic, key=key,
                                       payload={**payload, 'event_id': str(event_id), 'schema_version': SCHEMA_VERSION},
                                       schema_version=SCHEMA_VERSION))
    return event_id

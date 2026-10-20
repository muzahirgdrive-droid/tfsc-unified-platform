"""UUIDv7 (RFC 9562): time-ordered ids so B-tree inserts stay append-mostly."""
from __future__ import annotations

import os
import time
import uuid


def uuid7(ms: int | None = None) -> uuid.UUID:
    ts = int(time.time() * 1000) if ms is None else ms
    rand = int.from_bytes(os.urandom(10), 'big')
    value = (ts & 0xFFFFFFFFFFFF) << 80
    value |= 0x7 << 76
    value |= (rand >> 68 & 0xFFF) << 64
    value |= 0b10 << 62
    value |= rand & 0x3FFFFFFFFFFFFFFF
    return uuid.UUID(int=value)

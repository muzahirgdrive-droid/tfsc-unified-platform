"""Virus scan of every upload before it is stored (§4.2): ClamAV's clamd over TCP (INSTREAM).

CRM_CLAMAV = `host:port` (e.g. clamav:3310) or `off`. Hosted mode refuses to start without an explicit value. A file
clamd reports as infected is refused (422) and the attempt is audited by the caller; if clamd cannot be reached the
upload is refused too (503): an unscanned file is never stored while scanning is configured.
"""
from __future__ import annotations

import socket
import struct
from typing import Any

from fastapi import HTTPException

from ..config import get_settings

CHUNK = 64 * 1024


class Infected(HTTPException):
    def __init__(self, signature: str):
        super().__init__(422, 'The file was refused by the virus scanner.')
        self.signature = signature


def enabled() -> bool:
    return get_settings().clamav not in ('', 'off')


def _instream(data: bytes, timeout: float) -> str:
    host, _, port = get_settings().clamav.rpartition(':')
    with socket.create_connection((host, int(port)), timeout=timeout) as s:
        s.sendall(b'zINSTREAM\0')
        for i in range(0, len(data), CHUNK):
            part = data[i:i + CHUNK]
            s.sendall(struct.pack('>I', len(part)) + part)
        s.sendall(struct.pack('>I', 0))
        reply = b''
        while not reply.endswith(b'\0'):
            got = s.recv(4096)
            if not got:
                break
            reply += got
    return reply.rstrip(b'\0').decode('utf-8', 'replace')


def scan(data: bytes, timeout: float = 60) -> None:
    """Raises Infected (422) for a detected file, HTTPException 503 when the scanner is unavailable."""
    if not enabled():
        return
    try:
        reply = _instream(data, timeout)
    except OSError:
        raise HTTPException(503, 'The virus scanner is unavailable. Try the upload again shortly.') from None
    if reply.endswith('FOUND'):
        raise Infected(reply.removeprefix('stream:').removesuffix('FOUND').strip())
    if not reply.endswith('OK'):  # e.g. "INSTREAM size limit exceeded. ERROR"
        raise HTTPException(503, 'The virus scanner could not check this file. Try again or use a smaller file.')


async def check_upload(actor: Any, data: bytes, filename: str) -> None:
    """Scan an upload off the event loop; a detection is audited before the 422 goes back."""
    from starlette.concurrency import run_in_threadpool
    try:
        await run_in_threadpool(scan, data)
    except Infected as e:
        from ..db.session import tx
        from ..domain import audit
        with tx(actor.org_id, actor.id) as c:
            audit.record(c, actor.org_id, actor.id, 'file.rejected', 'upload', None, None,
                         {'filename': filename[:200], 'bytes': len(data), 'signature': e.signature[:200]})
        raise

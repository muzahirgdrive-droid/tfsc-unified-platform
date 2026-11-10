"""Scripts and style sheets are cached by the browser for a year, safely.

Every page names its scripts and style sheets with the release's fingerprint (/app.js?v=3f9c2a71b0d4). A file asked for
with the current fingerprint may be kept "for ever" (a new release has a new fingerprint, so its files are fetched afresh);
without it the browser checks back each time. After the first visit a page load needs one or two requests instead of
eighteen, and none of them touches the database: the files hold no client data.
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

APP = Path(__file__).resolve().parent
_REFERENCE = re.compile(r'(?P<attr>\b(?:src|href))="/(?P<name>[A-Za-z0-9_.-]+\.(?:js|css))"')


@lru_cache(maxsize=1)
def version() -> str:
    digest = hashlib.sha256()
    for path in sorted(APP.glob('*.js')) + sorted(APP.glob('*.css')):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


@lru_cache(maxsize=16)
def page(name: str) -> str:
    """An HTML page of the application with fingerprinted script and style sheet addresses."""
    text = (APP / name).read_text(encoding='utf-8')
    return _REFERENCE.sub(lambda m: f'{m.group("attr")}="/{m.group("name")}?v={version()}"', text)


def cache_header(requested: str | None) -> str:
    return 'public, max-age=31536000, immutable' if requested == version() else 'no-cache'

"""HTML helpers shared by Discovery providers."""

from __future__ import annotations

import re
from html import unescape

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str | None, *, limit: int = 1200) -> str | None:
    if not text:
        return None
    cleaned = unescape(_TAG_RE.sub(" ", text))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None
    return cleaned[:limit]

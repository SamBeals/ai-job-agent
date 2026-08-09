"""Conservative work-arrangement and location helpers."""

from __future__ import annotations

import re


def detect_remote_status(text: str) -> str | None:
    """Return remote|hybrid|onsite only when explicitly stated."""
    if not text:
        return None
    t = text.lower()

    # Prefer explicit labeled fields
    labeled = re.search(
        r"(?:work\s*arrangement|work\s*type|location\s*type|workplace)\s*[:\-]\s*(remote|hybrid|on[\s\-]?site|in[\s\-]?office)",
        t,
    )
    if labeled:
        return _normalize_status(labeled.group(1))

    if re.search(r"\b(fully\s+)?remote\b", t) and not re.search(r"\bnot\s+remote\b", t):
        # Avoid inferring from "remote employees elsewhere"
        if re.search(r"\b(position|role|job|this\s+role)\b.{0,40}\bremote\b|\bremote\b.{0,40}\b(position|role|job)\b", t):
            return "remote"
        if re.search(r"\b(remote\s*[-–—]\s*us|remote\s+us|work\s+from\s+home|wfh)\b", t):
            return "remote"
        # Title/location line often says "Remote"
        if re.search(r"(?m)^\s*remote\s*$", t) or re.search(r"\blocation\s*:\s*remote\b", t):
            return "remote"

    if re.search(r"\bhybrid\b", t):
        return "hybrid"

    if re.search(r"\b(on[\s\-]?site|in[\s\-]?office|in\s+person)\b", t):
        return "onsite"

    return None


def _normalize_status(value: str) -> str:
    v = value.lower().replace("_", " ").replace("-", " ")
    v = " ".join(v.split())
    if "remote" in v:
        return "remote"
    if "hybrid" in v:
        return "hybrid"
    return "onsite"


def detect_location_line(text: str) -> str | None:
    """Pull a likely location string without inventing one."""
    if not text:
        return None
    patterns = [
        r"(?im)^\s*location\s*[:\-]\s*(.+)$",
        r"(?im)^\s*(?:based\s+in|office)\s*[:\-]\s*(.+)$",
        # US-style City, ST — state code must be uppercase (no IGNORECASE)
        r"\b([A-Z][a-zA-Z .'-]+,\s*[A-Z]{2})\b",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            loc = m.group(1).strip()
            if len(loc) < 80 and "http" not in loc.lower():
                # Reject obvious non-locations
                if loc.lower().startswith(("and ", "the ", "with ", "from ")):
                    continue
                return loc
    return None

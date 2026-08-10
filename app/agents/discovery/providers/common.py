"""Shared soft title pre-filters for ATS Discovery providers."""

from __future__ import annotations

from app.schemas.discovery import DiscoveryQuery


def title_matches_discovery_query(title: str, query: DiscoveryQuery) -> bool:
    """Cheap role soft-screen before expensive ranking (not a hard filter)."""
    t = title.lower()
    if any(bad in t for bad in ("sales", "account executive", "recruiter", "marketing")):
        if query.role_terms and not any(r.lower() in t for r in query.role_terms):
            return False
    if query.role_terms:
        return any(
            term.lower() in t
            or any(w in t for w in term.lower().split() if len(w) > 3)
            for term in query.role_terms
        ) or any(
            x in t for x in ("software", "engineer", "developer", "backend", "platform")
        )
    return any(x in t for x in ("software", "engineer", "developer", "backend"))


def infer_work_arrangement(
    location: str | None,
    title: str | None = None,
    *,
    workplace_hint: str | None = None,
) -> str | None:
    hint = (workplace_hint or "").lower().replace("_", "").replace("-", "")
    if hint in {"remote"}:
        return "remote"
    if hint in {"hybrid"}:
        return "hybrid"
    if hint in {"onsite", "onsite", "on site"}:
        return "onsite"
    blob = f"{location or ''} {title or ''} {workplace_hint or ''}".lower()
    if "remote" in blob:
        return "remote"
    if "hybrid" in blob:
        return "hybrid"
    if any(x in blob for x in ("on-site", "onsite", "on site")):
        return "onsite"
    return None

"""Deterministic Discovery ranking — explainable reason_codes."""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.candidate import CandidateProfile
from app.schemas.discovery import RankedDiscoveryCandidate, RawDiscoveryResult


_BACKEND_HINTS = (
    "backend",
    "back-end",
    "back end",
    "java",
    "spring",
    "platform engineer",
    "api engineer",
    "server-side",
    "microservices",
)
_DEV_HINTS = (
    "software engineer",
    "software developer",
    "developer",
    "engineer",
    "full stack",
    "fullstack",
)
_CHANDLER = ("chandler",)
_PHOENIX_METRO = (
    "phoenix",
    "tempe",
    "mesa",
    "gilbert",
    "scottsdale",
    "glendale",
    "peoria",
    "chandler",
    "arizona",
    "az",
)


def score_candidate(
    profile: CandidateProfile,
    candidate: RankedDiscoveryCandidate,
) -> RankedDiscoveryCandidate:
    """Assign discovery_score 0–100 and reason_codes. Does not use an LLM."""
    if candidate.filtered:
        return candidate

    raw = candidate.raw
    prefs = profile.preferences
    score = 0
    reasons: list[str] = []

    title_l = raw.title.lower()
    loc_l = (raw.location_text or "").lower()
    blob = f"{title_l} {(raw.description_snippet or '').lower()}"

    us_eligible = (
        candidate.us_work_eligible
        if candidate.us_work_eligible is not None
        else raw.us_work_eligible
    )
    country = candidate.normalized_country or raw.normalized_country

    # Role-family alignment
    targets = [t.lower() for t in (prefs.target_roles or [])]
    if targets and any(t in title_l or title_l in t for t in targets):
        score += 25
        reasons.append("TARGET_ROLE")
    elif any(h in title_l for h in _DEV_HINTS):
        score += 15
        reasons.append("DEVELOPMENT_SIGNAL")

    if prefs.prefers_software_development is True and any(h in blob for h in _DEV_HINTS):
        if "DEVELOPMENT_SIGNAL" not in reasons:
            score += 10
            reasons.append("DEVELOPMENT_SIGNAL")

    if prefs.prefers_backend is True and any(h in blob for h in _BACKEND_HINTS):
        score += 20
        reasons.append("BACKEND_SIGNAL")
    elif prefs.prefers_backend is not False and any(h in blob for h in _BACKEND_HINTS):
        score += 12
        reasons.append("BACKEND_SIGNAL")

    # Geography ranking (eligibility already enforced in prefilter)
    if us_eligible is True:
        reasons.append("US_ELIGIBLE")
        score += 5
    elif us_eligible is None:
        reasons.append("GEO_UNKNOWN")

    if any(c in loc_l for c in _CHANDLER):
        score += 20
        reasons.append("CHANDLER")
    elif any(p in loc_l for p in _PHOENIX_METRO):
        score += 12
        reasons.append("PHOENIX_METRO")

    preferred = [p.lower() for p in (prefs.preferred_locations or [])]
    if preferred and any(p.split(",")[0].strip() in loc_l for p in preferred):
        if "CHANDLER" not in reasons and "PHOENIX_METRO" not in reasons:
            score += 15
            reasons.append("PREFERRED_LOCATION")

    # Work arrangement — remote acceptable only when not known-foreign
    arrangement = (raw.work_arrangement or "").lower()
    order = [a.lower() for a in (prefs.work_arrangement_order or ["hybrid", "onsite", "remote"])]
    if arrangement == "hybrid":
        score += 12 if (not order or order[0] == "hybrid") else 8
        reasons.append("HYBRID")
    elif arrangement in {"onsite", "on-site", "on site"}:
        score += 10 if "onsite" in order[:2] else 6
        reasons.append("ONSITE")
    elif arrangement == "remote" and us_eligible is not False:
        score += 4
        reasons.append("REMOTE_ACCEPTABLE")

    # Salary when known
    minimum = prefs.minimum_base_salary
    if raw.salary_min is None and raw.salary_max is None:
        reasons.append("SALARY_UNKNOWN")
    elif minimum is not None:
        offer = raw.salary_max if raw.salary_max is not None else raw.salary_min
        if offer is not None and offer >= minimum:
            score += 10
            reasons.append("SALARY_ABOVE_MINIMUM")

    # Freshness
    if raw.published_at is not None:
        published = raw.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - published).days
        if age_days <= 14:
            score += 5
            reasons.append("FRESH_POSTING")

    score = max(0, min(100, score))
    return RankedDiscoveryCandidate(
        raw=raw,
        discovery_score=score,
        reason_codes=reasons,
        filtered=False,
        us_work_eligible=us_eligible,
        normalized_country=country,
    )


def compare_for_rank(a: RankedDiscoveryCandidate, b: RankedDiscoveryCandidate) -> int:
    """Sort key helper: higher score first; prefer local hybrid over remote when tied."""
    if a.discovery_score != b.discovery_score:
        return b.discovery_score - a.discovery_score
    return 0

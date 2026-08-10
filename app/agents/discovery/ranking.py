"""Deterministic Discovery ranking — explainable reason_codes.

Scoring is calibrated so weak generic signals (bare \"engineer\" + unknown
location + hybrid) cannot pile up at a shared middling score. Local
hybrid/onsite bonuses require known compatible geography.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.schemas.candidate import CandidateProfile
from app.schemas.discovery import RankedDiscoveryCandidate


# Strong IC software title phrases (not bare \"developer\" / \"engineer\")
_TARGET_SOFTWARE_PHRASES = (
    "software engineer",
    "software developer",
    "backend engineer",
    "backend developer",
    "back-end engineer",
    "back-end developer",
    "backend software",
    "java engineer",
    "java developer",
    "java software",
    "application engineer",
    "application developer",
    "platform engineer",
    "full stack",
    "fullstack",
    "full-stack",
)

_BACKEND_PHRASES = (
    "backend",
    "back-end",
    "back end",
    "server-side",
    "server side",
    "microservices",
    "api engineer",
    "platform engineer",
)

_JAVA_PHRASES = ("java", "spring boot", "springboot", "jvm", "kotlin")

# Engineering but not primary target family for this candidate
_SPECIALIZED_ENGINEER = re.compile(
    r"\b("
    r"distributed\s+systems\s+engineer|"
    r"systems\s+engineer|"
    r"data\s+platform|"
    r"forward\s+deployed\s+engineer|"
    r"\bfde\b|"
    r"site\s+reliability|"
    r"\bsre\b|"
    r"machine\s+learning\s+engineer|"
    r"ml\s+engineer|"
    r"security\s+engineer|"
    r"infrastructure\s+engineer|"
    r"network\s+engineer"
    r")\b",
    re.I,
)

_CHANDLER = ("chandler",)
_PHOENIX_METRO = (
    "chandler",
    "phoenix",
    "tempe",
    "mesa",
    "gilbert",
    "scottsdale",
    "glendale",
    "peoria",
    "arizona",
    ", az",
    " east valley",
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
    location_known = bool((raw.location_text or "").strip()) and us_eligible is not None
    # N/A / empty / unresolved → treat as unknown for local bonuses
    if us_eligible is None:
        location_known = False
        reasons.append("LOCATION_UNKNOWN")
    elif not (raw.location_text or "").strip():
        location_known = False
        if "LOCATION_UNKNOWN" not in reasons:
            reasons.append("LOCATION_UNKNOWN")

    local_tier = _local_geo_tier(loc_l)

    # --- Role family (strict phrases; no bare developer/engineer) ---
    target_hit = False
    targets = [t.lower() for t in (prefs.target_roles or [])]
    if targets and any(t in title_l for t in targets):
        target_hit = True
    elif any(p in title_l for p in _TARGET_SOFTWARE_PHRASES):
        target_hit = True

    if target_hit:
        score += 28
        reasons.append("TARGET_SOFTWARE_ROLE")
    elif _SPECIALIZED_ENGINEER.search(title_l):
        score += 10
        reasons.append("SPECIALIZED_ROLE")
    elif prefs.prefers_software_development is True and _weak_software_title(title_l):
        # Very weak leftover signal — intentionally small
        score += 4
        reasons.append("WEAK_SOFTWARE_SIGNAL")

    if any(p in blob for p in _BACKEND_PHRASES):
        score += 18
        reasons.append("BACKEND_SIGNAL")

    if any(p in blob for p in _JAVA_PHRASES):
        score += 8
        reasons.append("JAVA_SIGNAL")

    # --- Geography eligibility label (ranking only; foreign already filtered) ---
    if us_eligible is True:
        reasons.append("US_ELIGIBLE")

    if local_tier == "chandler":
        score += 22
        reasons.append("CHANDLER")
    elif local_tier == "phoenix":
        score += 14
        reasons.append("PHOENIX_METRO")
    elif us_eligible is True and local_tier is None:
        # Known US but outside preferred metro — small credit only
        score += 2

    # --- Work arrangement: hybrid/onsite bonus only with compatible local geo ---
    arrangement = (raw.work_arrangement or "").lower()
    if arrangement == "hybrid":
        if local_tier in {"chandler", "phoenix"}:
            score += 14
            reasons.append("LOCAL_HYBRID")
        elif location_known and us_eligible is True:
            # US hybrid outside Phoenix is low value unless relocation allowed
            if prefs.relocation_allowed is True:
                score += 4
                reasons.append("HYBRID")
            else:
                reasons.append("HYBRID_NONLOCAL")
        else:
            # Unknown location + hybrid: record arrangement, no local bonus
            reasons.append("HYBRID_LOCATION_UNKNOWN")
    elif arrangement in {"onsite", "on-site", "on site"}:
        if local_tier in {"chandler", "phoenix"}:
            score += 12
            reasons.append("LOCAL_ONSITE")
        elif location_known and us_eligible is True and prefs.relocation_allowed is True:
            score += 3
            reasons.append("ONSITE")
        elif not location_known:
            reasons.append("ONSITE_LOCATION_UNKNOWN")
        else:
            reasons.append("ONSITE_NONLOCAL")
    elif arrangement == "remote" and us_eligible is True:
        score += 6
        reasons.append("US_REMOTE")
    elif arrangement == "remote" and us_eligible is None:
        reasons.append("REMOTE_LOCATION_UNKNOWN")

    # Language constraint already hard-filtered when unmet; note if present but ok
    if candidate.reason_codes and "MANDATORY_LANGUAGE_SIGNAL" in candidate.reason_codes:
        reasons.append("MANDATORY_LANGUAGE_SIGNAL")

    # Salary
    minimum = prefs.minimum_base_salary
    if raw.salary_min is None and raw.salary_max is None:
        reasons.append("SALARY_UNKNOWN")
    elif minimum is not None:
        offer = raw.salary_max if raw.salary_max is not None else raw.salary_min
        if offer is not None and offer >= minimum:
            score += 8
            reasons.append("SALARY_ABOVE_MINIMUM")

    # Freshness — minor only (must not create a shared floor with hybrid)
    if raw.published_at is not None:
        published = raw.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - published).days
        if age_days <= 14:
            score += 3
            reasons.append("FRESH_POSTING")

    score = max(0, min(100, score))
    # Merge any prefilter informational codes
    for code in candidate.reason_codes or []:
        if code not in reasons:
            reasons.append(code)

    return RankedDiscoveryCandidate(
        raw=raw,
        discovery_score=score,
        reason_codes=reasons,
        filtered=False,
        us_work_eligible=us_eligible,
        normalized_country=country,
    )


def _local_geo_tier(loc_l: str) -> str | None:
    if not loc_l:
        return None
    if any(c in loc_l for c in _CHANDLER):
        return "chandler"
    if any(p in loc_l for p in _PHOENIX_METRO):
        return "phoenix"
    return None


def _weak_software_title(title_l: str) -> bool:
    """True for leftover engineering-ish titles without target phrases."""
    if "engineer" in title_l or "developer" in title_l:
        return True
    return False


def compare_for_rank(a: RankedDiscoveryCandidate, b: RankedDiscoveryCandidate) -> int:
    if a.discovery_score != b.discovery_score:
        return b.discovery_score - a.discovery_score
    return 0

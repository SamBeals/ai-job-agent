"""Deterministic Discovery ranking — explainable reason_codes.

Geographic viability dominates preference ranking:
preferred-metro local hybrid/onsite ≫ US remote ≫ remote-unknown.
Nonlocal physical roles are hard-filtered upstream when relocation is not allowed.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.agents.discovery.viability import assess_viability, relocation_explicitly_allowed
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
    r"network\s+engineer|"
    r"devsecops"
    r")\b",
    re.I,
)

# Without preferred-metro or verified US-remote viability, role signals alone
# must stay below DISCOVERY_MIN_SURFACE_SCORE (45).
_MAX_SCORE_WITHOUT_GEO_VIABILITY = 40


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

    viability = assess_viability(
        prefs,
        location_text=raw.location_text,
        work_arrangement=raw.work_arrangement,
    )
    us_eligible = (
        candidate.us_work_eligible
        if candidate.us_work_eligible is not None
        else viability.us_work_eligible
    )
    country = candidate.normalized_country or viability.normalized_country

    # Carry viability reason codes (deduped later with scoring codes)
    for code in viability.reason_codes:
        if code not in reasons:
            reasons.append(code)
    if us_eligible is None and "LOCATION_UNKNOWN" not in reasons:
        if not viability.remote_us_eligible and not viability.in_preferred_metro:
            reasons.append("LOCATION_UNKNOWN")

    # --- Role family ---
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
        score += 4
        reasons.append("WEAK_SOFTWARE_SIGNAL")

    if any(p in blob for p in _BACKEND_PHRASES):
        score += 18
        reasons.append("BACKEND_SIGNAL")

    if any(p in blob for p in _JAVA_PHRASES):
        score += 8
        reasons.append("JAVA_SIGNAL")

    # --- Locality / arrangement (no generic Hybrid reward) ---
    geo_viable = False
    if viability.metro_tier == "preferred":
        score += 22
        geo_viable = True
        if "CHANDLER" not in reasons and "chandler" in loc_l:
            reasons.append("CHANDLER")
    elif viability.metro_tier == "acceptable":
        score += 14
        geo_viable = True

    if viability.arrangement == "hybrid" and viability.in_preferred_metro:
        score += 14
        if "LOCAL_HYBRID" not in reasons:
            reasons.append("LOCAL_HYBRID")
        geo_viable = True
    elif viability.arrangement == "onsite" and viability.in_preferred_metro:
        score += 12
        if "LOCAL_ONSITE" not in reasons:
            reasons.append("LOCAL_ONSITE")
        geo_viable = True
    elif viability.remote_us_eligible:
        score += 6
        if "US_REMOTE" not in reasons:
            reasons.append("US_REMOTE")
        geo_viable = True
    elif viability.remote_eligibility_unknown:
        if "REMOTE_ELIGIBILITY_UNKNOWN" not in reasons:
            reasons.append("REMOTE_ELIGIBILITY_UNKNOWN")
    elif viability.nonlocal_physical and relocation_explicitly_allowed(prefs):
        # Relocation explicitly permitted — eligible but not preferred
        score += 4
        if viability.arrangement == "hybrid":
            reasons.append("HYBRID")
        elif viability.arrangement == "onsite":
            reasons.append("ONSITE")
        else:
            reasons.append("RELOCATION_ELIGIBLE_NONLOCAL")
        geo_viable = True
    elif viability.arrangement == "hybrid" and not viability.in_preferred_metro:
        if "NONLOCAL_HYBRID" not in reasons:
            reasons.append("NONLOCAL_HYBRID")
    elif viability.arrangement == "onsite" and not viability.in_preferred_metro:
        if "NONLOCAL_ONSITE" not in reasons:
            reasons.append("NONLOCAL_ONSITE")

    # Language constraint already hard-filtered when unmet
    if candidate.reason_codes and "MANDATORY_LANGUAGE_SIGNAL" in candidate.reason_codes:
        if "MANDATORY_LANGUAGE_SIGNAL" not in reasons:
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

    # Freshness — minor only
    if raw.published_at is not None:
        published = raw.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - published).days
        if age_days <= 14:
            score += 3
            reasons.append("FRESH_POSTING")

    if not geo_viable:
        score = min(score, _MAX_SCORE_WITHOUT_GEO_VIABILITY)

    score = max(0, min(100, score))

    for code in candidate.reason_codes or []:
        if code not in reasons:
            reasons.append(code)

    # Deduplicate while preserving order
    deduped_reasons: list[str] = []
    for code in reasons:
        if code not in deduped_reasons:
            deduped_reasons.append(code)

    return RankedDiscoveryCandidate(
        raw=raw,
        discovery_score=score,
        reason_codes=deduped_reasons,
        filtered=False,
        us_work_eligible=us_eligible,
        normalized_country=country,
    )


def _weak_software_title(title_l: str) -> bool:
    if "engineer" in title_l or "developer" in title_l:
        return True
    return False


def compare_for_rank(a: RankedDiscoveryCandidate, b: RankedDiscoveryCandidate) -> int:
    if a.discovery_score != b.discovery_score:
        return b.discovery_score - a.discovery_score
    return 0

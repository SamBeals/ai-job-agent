"""Build DiscoveryQuery plans from candidate preferences."""

from __future__ import annotations

from app.schemas.candidate import CandidateProfile
from app.schemas.discovery import DiscoveryQuery
from app.schemas.preferences import JobPreferences


DEFAULT_ROLE_TERMS = [
    "Backend Software Engineer",
    "Backend Engineer",
    "Software Engineer",
    "Java Engineer",
    "Java Developer",
    "Software Developer",
    "Platform Engineer",
    "Application Engineer",
    "Senior Software Engineer",
]

DEFAULT_LOCATION_TERMS = [
    "Chandler, AZ",
    "Phoenix, AZ",
    "Tempe, AZ",
    "Mesa, AZ",
    "Gilbert, AZ",
]


def plan_discovery_query(
    profile: CandidateProfile,
    *,
    max_raw_results: int = 100,
) -> DiscoveryQuery:
    """Central query planning from stored preferences (no hardcoding of salary floor)."""
    prefs = profile.preferences
    role_terms = _role_terms(prefs)
    location_terms = _location_terms(prefs)
    include_remote = True
    if prefs.remote_required is True:
        include_remote = True
    elif prefs.remote_required is False and prefs.hybrid_allowed is False and prefs.onsite_allowed is False:
        # Extremely constrained — still allow remote unless explicitly impossible
        include_remote = True

    return DiscoveryQuery(
        role_terms=role_terms,
        location_terms=location_terms,
        include_remote=include_remote,
        minimum_base_salary=prefs.minimum_base_salary,
        prefers_backend=prefs.prefers_backend,
        prefers_software_development=prefs.prefers_software_development,
        excluded_roles=list(prefs.excluded_roles or []),
        max_raw_results=max_raw_results,
    )


def query_debug_lines(query: DiscoveryQuery) -> list[str]:
    """Safe query text for logging / DiscoveryRun.queries_executed."""
    lines = [
        f"roles={', '.join(query.role_terms[:8])}",
        f"locations={', '.join(query.location_terms[:8])}",
        f"include_remote={query.include_remote}",
        f"min_salary={query.minimum_base_salary}",
        f"max_raw={query.max_raw_results}",
    ]
    return lines


def _role_terms(prefs: JobPreferences) -> list[str]:
    terms: list[str] = []
    for group in (prefs.target_roles, prefs.acceptable_roles):
        if group:
            terms.extend(group)
    if not terms:
        terms = list(DEFAULT_ROLE_TERMS)
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        key = t.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(t.strip())
    return out


def _location_terms(prefs: JobPreferences) -> list[str]:
    terms: list[str] = []
    for group in (prefs.preferred_locations, prefs.acceptable_locations):
        if group:
            terms.extend(group)
    if prefs.home_location:
        terms.append(prefs.home_location)
    if not terms:
        terms = list(DEFAULT_LOCATION_TERMS)
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        key = t.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(t.strip())
    return out

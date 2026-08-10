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

# Preferred-metro fallbacks when a profile omits location prefs.
# Architecture stays profile-driven; these are only defaults.
DEFAULT_LOCATION_TERMS = [
    "Chandler, AZ",
    "Tempe, AZ",
    "Phoenix, AZ",
    "Scottsdale, AZ",
    "Mesa, AZ",
    "Gilbert, AZ",
]

# Consolidated role hints for Type-B broad search (avoid title×city explosion).
_BROAD_LOCAL_ROLE_HINTS = (
    "Software Engineer",
    "Backend Engineer",
    "Java Engineer",
)

_MAX_LOCAL_LOCATIONS = 6
_MAX_BROAD_LOCAL_ROLES = 3


def plan_discovery_query(
    profile: CandidateProfile,
    *,
    max_raw_results: int = 100,
) -> DiscoveryQuery:
    """Central query planning from stored preferences (no hardcoding of salary floor)."""
    prefs = profile.preferences
    role_terms = _role_terms(prefs)
    location_terms = _location_terms(prefs)
    local_location_terms = _normalize_local_locations(location_terms)
    include_remote = True
    if prefs.remote_required is True:
        include_remote = True
    elif prefs.remote_required is False and prefs.hybrid_allowed is False and prefs.onsite_allowed is False:
        # Extremely constrained — still allow remote unless explicitly impossible
        include_remote = True

    return DiscoveryQuery(
        role_terms=role_terms,
        location_terms=location_terms,
        local_location_terms=local_location_terms,
        include_remote=include_remote,
        prioritize_local_search=True,
        minimum_base_salary=prefs.minimum_base_salary,
        prefers_backend=prefs.prefers_backend,
        prefers_software_development=prefs.prefers_software_development,
        excluded_roles=list(prefs.excluded_roles or []),
        max_raw_results=max_raw_results,
    )


def query_debug_lines(query: DiscoveryQuery) -> list[str]:
    """Safe query text for logging / DiscoveryRun.queries_executed."""
    logical = plan_broad_search_logical_queries(query)
    local_n = sum(1 for q in logical if q["bucket"] == "local")
    remote_n = sum(1 for q in logical if q["bucket"] == "remote")
    lines = [
        f"roles={', '.join(query.role_terms[:8])}",
        f"locations={', '.join(query.location_terms[:8])}",
        f"local_locations={', '.join(query.local_location_terms[:8])}",
        f"include_remote={query.include_remote}",
        f"prioritize_local={query.prioritize_local_search}",
        f"broad_logical_local={local_n}",
        f"broad_logical_remote={remote_n}",
        f"min_salary={query.minimum_base_salary}",
        f"max_raw={query.max_raw_results}",
    ]
    return lines


def plan_broad_search_logical_queries(query: DiscoveryQuery) -> list[dict[str, str | None]]:
    """Return consolidated (role, location, bucket) requests for Type-B providers.

    Local searches come first. Remote remains available but does not dominate
    the request budget. Caps prevent pathological title × city fan-out.
    """
    locations = list(query.local_location_terms or [])
    if not locations:
        locations = _normalize_local_locations(query.location_terms or [])
    if not locations:
        locations = list(DEFAULT_LOCATION_TERMS)
    locations = locations[:_MAX_LOCAL_LOCATIONS]

    roles = _broad_local_roles(query)
    out: list[dict[str, str | None]] = []
    seen: set[tuple[str | None, str | None]] = set()

    def _add(role: str | None, location: str | None, bucket: str) -> None:
        key = (role, location)
        if key in seen:
            return
        seen.add(key)
        out.append({"role": role, "location": location, "bucket": bucket})

    # LOCAL SEARCH (priority)
    for loc in locations:
        for role in roles:
            _add(role, loc, "local")

    # US REMOTE SEARCH (retained, secondary)
    if query.include_remote:
        remote_role = roles[0] if roles else "Software Engineer"
        _add(remote_role, None, "remote")
        # Category-only remote sweep once (no second role × remote fan-out)
        if remote_role is not None:
            _add(None, None, "remote")

    return out


def _broad_local_roles(query: DiscoveryQuery) -> list[str]:
    picks: list[str] = []
    seen: set[str] = set()
    for hint in _BROAD_LOCAL_ROLE_HINTS:
        key = hint.lower()
        if key not in seen:
            seen.add(key)
            picks.append(hint)
        if len(picks) >= _MAX_BROAD_LOCAL_ROLES:
            return picks
    for r in query.role_terms or []:
        t = r.strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        if any(x in key for x in ("backend", "software", "java", "platform")):
            seen.add(key)
            picks.append(t)
        if len(picks) >= _MAX_BROAD_LOCAL_ROLES:
            break
    return picks or ["Software Engineer"]


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


def _normalize_local_locations(terms: list[str]) -> list[str]:
    """Normalize preference locations into City, ST for broad-search APIs."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in terms:
        normalized = _normalize_one_location(raw)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        # Skip vague metro blobs that don't help Type-B geo filters
        if any(x in key for x in ("metro", "east valley", "remote", "united states", "usa")):
            continue
        seen.add(key)
        out.append(normalized)
        if len(out) >= _MAX_LOCAL_LOCATIONS:
            break
    return out


def _normalize_one_location(raw: str) -> str | None:
    t = (raw or "").strip()
    if not t:
        return None
    # "Chandler, Arizona" → "Chandler, AZ"
    lower = t.lower()
    if ", arizona" in lower:
        city = t.split(",", 1)[0].strip()
        return f"{city}, AZ" if city else None
    if lower.endswith(" arizona"):
        city = t[: -len(" arizona")].strip(" ,")
        return f"{city}, AZ" if city else None
    return t

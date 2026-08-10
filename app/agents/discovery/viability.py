"""Discovery geographic viability — eligibility, locality, and arrangement.

Employment eligibility (US vs foreign) is separate from commuting locality
(preferred metro vs nonlocal physical) and from remote eligibility.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agents.discovery.geography import GeoAssessment, assess_geography
from app.schemas.preferences import JobPreferences


@dataclass(frozen=True)
class MetroProfile:
    """Candidate commuting metro derived from preferences (not hardcoded)."""

    preferred_tokens: tuple[str, ...]  # strongest (e.g. Chandler)
    acceptable_tokens: tuple[str, ...]  # metro (e.g. Phoenix, Tempe, …)
    home_tokens: tuple[str, ...]


@dataclass(frozen=True)
class ViabilityAssessment:
    """Structured geographic viability for Discovery prefilter/ranking."""

    us_work_eligible: bool | None
    normalized_country: str | None
    arrangement: str | None  # remote | hybrid | onsite | None
    metro_tier: str | None  # preferred | acceptable | None
    in_preferred_metro: bool
    nonlocal_physical: bool
    remote_us_eligible: bool
    remote_eligibility_unknown: bool
    remote_region_restricted: bool
    reason_codes: list[str] = field(default_factory=list)
    filter_reason: str | None = None
    geo_evidence: str = ""


_ARRANGEMENT_REMOTE = re.compile(
    r"\b(remote|work\s+from\s+home|\bwfh\b|flexible\s*/\s*remote|fully\s+remote)\b",
    re.I,
)
_ARRANGEMENT_HYBRID = re.compile(r"\bhybrid\b", re.I)
_ARRANGEMENT_ONSITE = re.compile(
    r"\b(on[- ]?site|in[- ]?office|office[- ]based)\b",
    re.I,
)

# Explicit remote restricted to a non-home US region (conservative)
_RESTRICTED_REMOTE = (
    r"\bremote\s*[-–—:]?\s*california\s+only\b",
    r"\bcalifornia\s+only\b.*\bremote\b",
    r"\bremote\s*\(\s*ca\s+only\s*\)",
    r"\bremote\s*[-–—:]?\s*new\s+york\s+only\b",
    r"\bnyc\s+only\b.*\bremote\b",
)


def metro_profile_from_preferences(prefs: JobPreferences) -> MetroProfile:
    """Build metro token sets from candidate preferences (profile-driven)."""
    preferred = _tokens_from_locations(prefs.preferred_locations or [])
    acceptable = _tokens_from_locations(prefs.acceptable_locations or [])
    home = _tokens_from_locations([prefs.home_location] if prefs.home_location else [])
    # Prefer preferred_locations for strongest tier; acceptable covers metro.
    # If only home/acceptable set, still treat those as commuting metro.
    if not preferred and not acceptable:
        acceptable = home
    return MetroProfile(
        preferred_tokens=tuple(preferred),
        acceptable_tokens=tuple(dict.fromkeys([*acceptable, *home])),
        home_tokens=tuple(home),
    )


def infer_arrangement(
    location_text: str | None,
    work_arrangement: str | None,
) -> str | None:
    """Normalize work arrangement from explicit field + location text."""
    raw = (work_arrangement or "").strip().lower().replace("_", " ").replace("-", " ")
    if raw in {"remote", "fully remote"}:
        return "remote"
    if raw == "hybrid":
        return "hybrid"
    if raw in {"onsite", "on site", "office"}:
        return "onsite"

    blob = f"{location_text or ''} {work_arrangement or ''}"
    # Prefer explicit onsite/hybrid over the word remote inside hybrid phrases
    if _ARRANGEMENT_HYBRID.search(blob):
        return "hybrid"
    if _ARRANGEMENT_ONSITE.search(blob) and not _ARRANGEMENT_REMOTE.search(blob):
        return "onsite"
    if _ARRANGEMENT_REMOTE.search(blob):
        return "remote"
    return None


def assess_viability(
    prefs: JobPreferences,
    *,
    location_text: str | None,
    work_arrangement: str | None,
    geo: GeoAssessment | None = None,
) -> ViabilityAssessment:
    """Combine US eligibility, preferred metro, and arrangement into viability."""
    geo = geo or assess_geography(location_text, work_arrangement=work_arrangement)
    arrangement = infer_arrangement(location_text, work_arrangement)
    metro = metro_profile_from_preferences(prefs)
    loc_l = (location_text or "").lower()

    metro_tier = _metro_tier(loc_l, metro)
    in_metro = metro_tier is not None

    remote_restricted = any(re.search(p, loc_l, re.I) for p in _RESTRICTED_REMOTE)
    # True US remote: arrangement remote AND (US-eligible geography OR explicit US remote markers)
    remote_us = False
    remote_unknown = False
    if arrangement == "remote":
        if remote_restricted and not in_metro:
            remote_us = False
        elif geo.us_work_eligible is True:
            remote_us = True
        elif geo.us_work_eligible is False:
            remote_us = False
        else:
            remote_unknown = True

    # Physical role: hybrid/onsite, or unknown arrangement with a concrete place name
    physical = arrangement in {"hybrid", "onsite"} or (
        arrangement is None and _looks_like_physical_place(loc_l, geo)
    )
    # Remote with a city listed (Dallas + remote) is remote, not nonlocal physical
    if arrangement == "remote":
        physical = False

    nonlocal_physical = False
    if physical and not in_metro:
        if geo.us_work_eligible is True:
            # Known US (or MULTI) place outside preferred commuting metro
            nonlocal_physical = True
        elif geo.us_work_eligible is None and _looks_like_physical_place(loc_l, geo):
            # Concrete place with unresolved country still isn't commute-viable
            nonlocal_physical = True
        # empty/N/A/ambiguous remote-only strings are not nonlocal physical

    reasons: list[str] = []
    if geo.us_work_eligible is True:
        reasons.append("US_ELIGIBLE")
    elif geo.us_work_eligible is False:
        reasons.append("FOREIGN_LOCATION")
    if metro_tier == "preferred":
        reasons.append("PREFERRED_METRO")
        # Specific token labels for ranking UX
        if any(t in loc_l for t in metro.preferred_tokens):
            if "chandler" in loc_l:
                reasons.append("CHANDLER")
            else:
                reasons.append("PREFERRED_LOCATION")
    elif metro_tier == "acceptable":
        reasons.append("PHOENIX_METRO" if _profile_is_phoenix_shaped(metro) else "ACCEPTABLE_METRO")

    if arrangement == "remote":
        if remote_us:
            reasons.append("US_REMOTE")
        elif remote_unknown:
            reasons.append("REMOTE_ELIGIBILITY_UNKNOWN")
        elif remote_restricted:
            reasons.append("REMOTE_REGION_RESTRICTED")
    elif arrangement == "hybrid":
        if in_metro:
            reasons.append("LOCAL_HYBRID")
        elif nonlocal_physical:
            reasons.append("NONLOCAL_HYBRID")
    elif arrangement == "onsite":
        if in_metro:
            reasons.append("LOCAL_ONSITE")
        elif nonlocal_physical:
            reasons.append("NONLOCAL_ONSITE")
    elif arrangement is None and nonlocal_physical:
        reasons.append("NONLOCAL_PHYSICAL_UNKNOWN")

    filter_reason = _filter_reason(
        prefs,
        geo=geo,
        arrangement=arrangement,
        in_metro=in_metro,
        nonlocal_physical=nonlocal_physical,
        remote_us=remote_us,
        remote_unknown=remote_unknown,
        remote_restricted=remote_restricted,
    )

    return ViabilityAssessment(
        us_work_eligible=geo.us_work_eligible,
        normalized_country=geo.normalized_country,
        arrangement=arrangement,
        metro_tier=metro_tier,
        in_preferred_metro=in_metro,
        nonlocal_physical=nonlocal_physical,
        remote_us_eligible=remote_us,
        remote_eligibility_unknown=remote_unknown,
        remote_region_restricted=remote_restricted,
        reason_codes=reasons,
        filter_reason=filter_reason,
        geo_evidence=geo.evidence,
    )


def relocation_explicitly_allowed(prefs: JobPreferences) -> bool:
    """Only True permits nonlocal physical roles; null/False do not."""
    return prefs.relocation_allowed is True


def _filter_reason(
    prefs: JobPreferences,
    *,
    geo: GeoAssessment,
    arrangement: str | None,
    in_metro: bool,
    nonlocal_physical: bool,
    remote_us: bool,
    remote_unknown: bool,
    remote_restricted: bool,
) -> str | None:
    if geo.us_work_eligible is False:
        return "FOREIGN_LOCATION"

    if remote_restricted and not in_metro:
        return "REMOTE_REGION_INCOMPATIBLE"

    if nonlocal_physical and not relocation_explicitly_allowed(prefs):
        if arrangement == "onsite":
            return "NONLOCAL_ONSITE"
        if arrangement == "hybrid":
            return "NONLOCAL_HYBRID"
        # Unknown arrangement at a nonlocal physical place — not commute-viable
        return "NONLOCAL_PHYSICAL_UNKNOWN"

    return None


def _metro_tier(loc_l: str, metro: MetroProfile) -> str | None:
    if not loc_l:
        return None
    if metro.preferred_tokens and any(t in loc_l for t in metro.preferred_tokens):
        return "preferred"
    if metro.acceptable_tokens and any(t in loc_l for t in metro.acceptable_tokens):
        return "acceptable"
    return None


def _tokens_from_locations(locations: list[str]) -> list[str]:
    tokens: list[str] = []
    for loc in locations:
        low = (loc or "").lower().strip()
        if not low:
            continue
        # City / metro phrases before state
        head = re.split(r"[,/|]", low)[0].strip()
        if head:
            tokens.append(head)
        # Also keep notable aliases present in the string
        for alias in (
            "chandler",
            "phoenix",
            "tempe",
            "mesa",
            "gilbert",
            "scottsdale",
            "glendale",
            "peoria",
            "east valley",
            "arizona",
        ):
            if alias in low and alias not in tokens:
                tokens.append(alias)
        if ", az" in low or re.search(r"\baz\b", low):
            if "arizona" not in tokens:
                tokens.append("arizona")
    # Prefer longer tokens first for matching stability (tuple order preserved unique)
    return list(dict.fromkeys(tokens))


def _profile_is_phoenix_shaped(metro: MetroProfile) -> bool:
    blob = " ".join([*metro.preferred_tokens, *metro.acceptable_tokens, *metro.home_tokens])
    return any(
        x in blob
        for x in ("phoenix", "chandler", "tempe", "mesa", "gilbert", "scottsdale", "arizona")
    )


def _looks_like_physical_place(loc_l: str, geo: GeoAssessment) -> bool:
    if not loc_l or geo.evidence in {"empty_or_na_location", "ambiguous_remote"}:
        return False
    if re.match(r"^\s*(remote|flexible\s*/\s*remote|fully\s+remote)\s*$", loc_l):
        return False
    # City, ST or named city patterns
    if re.search(r"\b[a-z].+,\s*[a-z]{2}\b", loc_l):
        return True
    if re.search(
        r"\b(new\s+york|washington|palo\s+alto|san\s+francisco|seattle|chicago|"
        r"dallas|austin|denver|boston|atlanta|springdale|colombia|bogot[aá])\b",
        loc_l,
    ):
        return True
    if geo.us_work_eligible is True and not re.search(r"\bremote\b", loc_l):
        return True
    return False

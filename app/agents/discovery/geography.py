"""Deterministic Discovery geography — US employment eligibility.

Remote means US-eligible remote when the candidate requires US employment.
Unknown geography must remain unknown (do not invent a country).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.preferences import JobPreferences


@dataclass(frozen=True)
class GeoAssessment:
    """Normalized geography for a Discovery opportunity."""

    us_work_eligible: bool | None
    """True = US-eligible, False = explicitly foreign, None = unknown."""

    normalized_country: str | None
    """ISO-ish label: US, CA, GB, IN, IE, AU, EU, MULTI, UNKNOWN, or None."""

    evidence: str = ""


# Explicit US employment markers (remote or onsite)
_US_MARKERS = (
    r"\bunited\s+states\b",
    r"\bu\.?\s*s\.?\s*a\.?\b",
    r"\bus[- ]?remote\b",
    r"\bremote\s*[-–—]?\s*us\b",
    r"\bremote\s*\(\s*us\b",
    r"\bremote\s*\(\s*us/",
    r"\bus\s*/\s*canada\b",  # US side available
    r"\b\(us/canada\)\b",
    r"\bin\s+the\s+us\b",
    r"\bbased\s+in\s+the\s+us\b",
)

# US state names / common abbreviations (word-boundary safe subset)
_US_STATES = (
    r"\barizona\b",
    r"\bcalifornia\b",
    r"\bwashington\b",
    r"\billinois\b",
    r"\bnew\s+york\b",
    r"\btexas\b",
    r"\bcolorado\b",
    r"\bgeorgia\b",
    r"\bflorida\b",
    r"\bmassachusetts\b",
    r"\boregon\b",
    r"\bnevada\b",
    r"\butah\b",
    r"\bpennsylvania\b",
    r"\bohio\b",
    r"\bnorth\s+carolina\b",
    r"\bvirginia\b",
    r"\bminnesota\b",
    r"\bmichigan\b",
    r"\b,\s*az\b",
    r"\b,\s*ca\b",
    r"\b,\s*wa\b",
    r"\b,\s*il\b",
    r"\b,\s*ny\b",
    r"\b,\s*tx\b",
    r"\b,\s*co\b",
    r"\b,\s*ga\b",
    r"\b,\s*fl\b",
    r"\b,\s*ma\b",
    r"\b,\s*or\b",
    r"\b,\s*nv\b",
    r"\b,\s*ut\b",
)

_US_CITIES = (
    r"\bchandler\b",
    r"\bphoenix\b",
    r"\btempe\b",
    r"\bmesa\b",
    r"\bgilbert\b",
    r"\bscottsdale\b",
    r"\bsan\s+francisco\b",
    r"\bseattle\b",
    r"\bchicago\b",
    r"\bnyc\b",
    r"\bnew\s+york\s+city\b",
    r"\baustin\b",
    r"\bdenver\b",
    r"\bboston\b",
    r"\batlanta\b",
    r"\bseattle\b",
    r"\blos\s+angeles\b",
    r"\bsan\s+diego\b",
    r"\bsan\s+jose\b",
)

_FOREIGN_COUNTRY = (
    (r"\bcanada\b", "CA"),
    (r"\bunited\s+kingdom\b", "GB"),
    (r"\bengland\b", "GB"),
    (r"\bscotland\b", "GB"),
    (r"\bwales\b", "GB"),
    (r"\b\buk\b", "GB"),
    (r"\bindia\b", "IN"),
    (r"\bireland\b", "IE"),
    (r"\baustralia\b", "AU"),
    (r"\bgermany\b", "EU"),
    (r"\bfrance\b", "EU"),
    (r"\bnetherlands\b", "EU"),
    (r"\bspain\b", "EU"),
    (r"\bportugal\b", "EU"),
    (r"\bpoland\b", "EU"),
    (r"\beurope\b", "EU"),
    (r"\bemea\b", "EU"),
    (r"\blatam\b", "LATAM"),
    (r"\bmexico\b", "MX"),
    (r"\bbrazil\b", "BR"),
    (r"\bsingapore\b", "SG"),
    (r"\bjapan\b", "JP"),
)

# Cities that are overwhelmingly non-US in job-board location strings
_FOREIGN_CITIES = (
    (r"\btoronto\b", "CA"),
    (r"\bvancouver\b", "CA"),
    (r"\bmontreal\b", "CA"),
    (r"\bottawa\b", "CA"),
    (r"\bbangalore\b", "IN"),
    (r"\bbengaluru\b", "IN"),
    (r"\bhyderabad\b", "IN"),
    (r"\bpune\b", "IN"),
    (r"\bchennai\b", "IN"),
    (r"\bmumbai\b", "IN"),
    (r"\bdelhi\b", "IN"),
    (r"\blondon\b", "GB"),
    (r"\bmanchester\b", "GB"),
    (r"\bedinburgh\b", "GB"),
    (r"\bsydney\b", "AU"),
    (r"\bmelbourne\b", "AU"),
    (r"\bberlin\b", "EU"),
    (r"\bamsterdam\b", "EU"),
    (r"\bparis\b", "EU"),
    (r"\bdublin\b", "IE"),  # Dublin CA is rare on ATS boards; Dublin, Ireland is common
)

_AMBIGUOUS_REMOTE = re.compile(
    r"^\s*(remote|fully\s+remote|work\s+from\s+home|wfh)\s*$",
    re.I,
)
_EMPTYISH = re.compile(r"^\s*(n/?a|none|null|unknown|-|—|\.|tbd|not\s+specified)?\s*$", re.I)


def requires_us_employment(prefs: JobPreferences) -> bool:
    """Whether Discovery should hard-reject explicitly foreign opportunities."""
    if prefs.us_employment_required is True:
        return True
    if prefs.us_employment_required is False:
        return False
    # Preference unset: infer only from clear US home geography (unknown stays off)
    home = (prefs.home_location or "").lower()
    if any(
        token in home
        for token in (
            "arizona",
            "united states",
            "u.s.",
            "usa",
            "phoenix",
            "chandler",
        )
    ):
        return True
    return False


def assess_geography(
    location_text: str | None,
    *,
    work_arrangement: str | None = None,
) -> GeoAssessment:
    """Normalize location_text into US eligibility without inventing missing facts."""
    text = (location_text or "").strip()
    if not text or _EMPTYISH.match(text):
        return GeoAssessment(
            us_work_eligible=None,
            normalized_country="UNKNOWN",
            evidence="empty_or_na_location",
        )

    # Bare ambiguous remote (no country)
    if _AMBIGUOUS_REMOTE.match(text):
        return GeoAssessment(
            us_work_eligible=None,
            normalized_country="UNKNOWN",
            evidence="ambiguous_remote",
        )

    # Split multi-location postings
    segments = _split_locations(text)
    assessments = [_assess_segment(seg) for seg in segments if seg.strip()]
    if not assessments:
        return GeoAssessment(
            us_work_eligible=None,
            normalized_country="UNKNOWN",
            evidence="unparsed",
        )

    us_hits = [a for a in assessments if a.us_work_eligible is True]
    foreign_hits = [a for a in assessments if a.us_work_eligible is False]
    unknown_hits = [a for a in assessments if a.us_work_eligible is None]

    if us_hits and foreign_hits:
        countries = sorted(
            {
                a.normalized_country
                for a in assessments
                if a.normalized_country and a.normalized_country != "UNKNOWN"
            }
        )
        return GeoAssessment(
            us_work_eligible=True,
            normalized_country="MULTI",
            evidence=f"us_plus_foreign:{','.join(countries)}",
        )
    if us_hits and not foreign_hits:
        country = us_hits[0].normalized_country or "US"
        if len(us_hits) > 1:
            country = "US"
        return GeoAssessment(
            us_work_eligible=True,
            normalized_country=country,
            evidence=us_hits[0].evidence,
        )
    if foreign_hits and not us_hits:
        # All explicit foreign (ignore unknowns mixed in only if every resolved is foreign)
        if unknown_hits and len(foreign_hits) + len(unknown_hits) == len(assessments):
            # e.g. "Remote; Canada" → foreign wins if Canada explicit
            pass
        countries = sorted(
            {
                a.normalized_country
                for a in foreign_hits
                if a.normalized_country
            }
        )
        return GeoAssessment(
            us_work_eligible=False,
            normalized_country=countries[0] if len(countries) == 1 else "MULTI",
            evidence=foreign_hits[0].evidence,
        )

    # Only unknowns
    arr = (work_arrangement or "").lower()
    if arr == "remote" and _AMBIGUOUS_REMOTE.match(text):
        return GeoAssessment(
            us_work_eligible=None,
            normalized_country="UNKNOWN",
            evidence="ambiguous_remote",
        )
    return GeoAssessment(
        us_work_eligible=None,
        normalized_country="UNKNOWN",
        evidence="unresolved_location",
    )


def _split_locations(text: str) -> list[str]:
    # Prefer semicolon / pipe / " or " separators used by Greenhouse/GitLab
    parts = re.split(r"\s*;\s*|\s*\|\s*|\s+or\s+", text, flags=re.I)
    if len(parts) == 1:
        # Comma lists of cities: "New York, San Francisco, Seattle" stay one US segment
        # But "Remote, Canada" is country — handled in segment assessor
        pass
    return [p.strip() for p in parts if p.strip()]


def _assess_segment(segment: str) -> GeoAssessment:
    s = segment.strip()
    low = s.lower()

    if _EMPTYISH.match(s) or _AMBIGUOUS_REMOTE.match(s):
        return GeoAssessment(None, "UNKNOWN", "ambiguous_or_empty_segment")

    # Standalone "US" / "USA"
    if re.fullmatch(r"us|usa|u\.s\.a?\.?", low):
        return GeoAssessment(True, "US", "country_us")

    us = any(re.search(p, low, re.I) for p in _US_MARKERS)
    us = us or any(re.search(p, low, re.I) for p in _US_STATES)
    us = us or any(re.search(p, low, re.I) for p in _US_CITIES)
    # NYC-Privy style
    if re.search(r"\bnyc\b", low):
        us = True

    foreign_country = None
    for pattern, code in _FOREIGN_COUNTRY:
        if re.search(pattern, low, re.I):
            foreign_country = code
            break
    if foreign_country is None:
        for pattern, code in _FOREIGN_CITIES:
            if re.search(pattern, low, re.I):
                # Guard: Dublin, CA / Dublin, California → US
                if code == "IE" and re.search(r"dublin\s*,\s*(ca|california)\b", low):
                    us = True
                    foreign_country = None
                else:
                    foreign_country = code
                break

    if us and foreign_country:
        # Segment itself mixes? uncommon; treat as US-eligible for that segment
        return GeoAssessment(True, "MULTI", f"segment_us_and_{foreign_country}")
    if us:
        return GeoAssessment(True, "US", "us_marker")
    if foreign_country:
        return GeoAssessment(False, foreign_country, f"foreign:{foreign_country}")

    # "Remote - Something" without known country → unknown
    if re.match(r"^\s*remote\b", low):
        return GeoAssessment(None, "UNKNOWN", "remote_unspecified_region")

    return GeoAssessment(None, "UNKNOWN", "unresolved_segment")

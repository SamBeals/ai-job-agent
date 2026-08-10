"""Cheap deterministic Discovery pre-filters (not Scout)."""

from __future__ import annotations

import re

from app.agents.discovery.geography import assess_geography, requires_us_employment
from app.agents.scout.hard_filters import apply_hard_filters
from app.schemas.candidate import CandidateProfile
from app.schemas.discovery import RankedDiscoveryCandidate, RawDiscoveryResult
from app.schemas.job_posting import NormalizedJob
from app.schemas.preferences import JobPreferences


# Obvious junk titles when preferences lean software/backend
_UNRELATED_TITLE_PATTERNS = [
    r"\bhelp\s*desk\b",
    r"\bdesktop\s+support\b",
    r"\bdata\s+entry\b",
    r"\bux\s+designer\b",
    r"\bui\s+designer\b",
    r"\baccount\s+executive\b",
    r"\bsales\s+engineer\b",
    r"\bsales\s+representative\b",
    r"\bsolutions?\s+engineer\b",
    r"\bcustomer\s+engineer\b",
    r"\bscrum\s+master\b",
    r"\bproject\s+manager\b",
    r"\bprogram\s+manager\b",
    r"\bproduct\s+manager\b",
    r"\brecruiter\b",
    r"\bcustomer\s+success\b",
]

# People-management tracks — not IC software-development target roles
_MANAGEMENT_TITLE_PATTERNS = [
    r"\bengineering\s+manager\b",
    r"\bmanager,?\s+software\s+engineering\b",
    r"\bsoftware\s+engineering\s+manager\b",
    r"\bmanager\s+of\s+(software\s+)?engineering\b",
    r"\bdirector\s+of\s+(software\s+)?engineering\b",
    r"\bvp\b.*\bengineering\b",
    r"\bhead\s+of\s+engineering\b",
    r"\bhead\s+of\s+software\b",
]

_FRONTEND_ONLY = re.compile(
    r"\b(frontend|front[- ]end)\b.*\b(engineer|developer)\b|"
    r"\b(engineer|developer)\b.*\b(frontend|front[- ]end)\b",
    re.I,
)
_INTERNSHIP = re.compile(r"\b(intern|internship|co[- ]?op)\b", re.I)

# IC software/development title families for prefers_software_development
_IC_DEV_TITLE = re.compile(
    r"\b("
    r"software\s+engineer|software\s+developer|backend|back[- ]end|"
    r"full[- ]?stack|platform\s+engineer|application\s+engineer|"
    r"java\s+(engineer|developer)|"
    r"site\s+reliability|sre|"
    r"devops\s+engineer|"
    r"api\s+engineer|"
    r"developer"
    r")\b",
    re.I,
)


def raw_to_normalized_job(raw: RawDiscoveryResult) -> NormalizedJob:
    """Minimal NormalizedJob for hard-filter reuse — not a full Scout ingest."""
    return NormalizedJob(
        external_id=raw.external_id,
        source=raw.provider,
        source_url=raw.canonical_url or raw.job_url,
        company=raw.company,
        title=raw.title,
        location=raw.location_text,
        remote_status=raw.work_arrangement,
        salary_min=raw.salary_min if raw.salary_period in (None, "year", "annual", "yearly") else None,
        salary_max=raw.salary_max if raw.salary_period in (None, "year", "annual", "yearly") else None,
        salary_currency=raw.salary_currency,
        description=raw.description_snippet or raw.description_full,
    )


def prefilter_candidate(
    profile: CandidateProfile,
    raw: RawDiscoveryResult,
) -> RankedDiscoveryCandidate:
    """Reject obvious mismatches; unknown salary/geography must not reject."""
    if raw.salary_period and raw.salary_period.lower() in {"hour", "hourly", "hr"}:
        adjusted = raw.model_copy(update={"salary_min": None, "salary_max": None})
    else:
        adjusted = raw

    prefs = profile.preferences
    geo = assess_geography(
        adjusted.location_text,
        work_arrangement=adjusted.work_arrangement,
    )
    adjusted = adjusted.model_copy(
        update={
            "normalized_country": geo.normalized_country,
            "us_work_eligible": geo.us_work_eligible,
        }
    )

    # Explicit foreign hard-reject when US employment is required
    if requires_us_employment(prefs) and geo.us_work_eligible is False:
        return RankedDiscoveryCandidate(
            raw=adjusted,
            filtered=True,
            filter_reason="FOREIGN_LOCATION",
            us_work_eligible=False,
            normalized_country=geo.normalized_country,
        )

    title = adjusted.title.lower()

    if _INTERNSHIP.search(title):
        targets = " ".join(prefs.target_roles or []).lower()
        if "intern" not in targets:
            return RankedDiscoveryCandidate(
                raw=adjusted,
                filtered=True,
                filter_reason="INTERNSHIP_NOT_TARGETED",
                us_work_eligible=geo.us_work_eligible,
                normalized_country=geo.normalized_country,
            )

    for pattern in _UNRELATED_TITLE_PATTERNS:
        if re.search(pattern, title, re.I):
            return RankedDiscoveryCandidate(
                raw=adjusted,
                filtered=True,
                filter_reason="UNRELATED_ROLE",
                us_work_eligible=geo.us_work_eligible,
                normalized_country=geo.normalized_country,
            )

    if _is_management_track(title, prefs):
        return RankedDiscoveryCandidate(
            raw=adjusted,
            filtered=True,
            filter_reason="MANAGEMENT_ROLE",
            us_work_eligible=geo.us_work_eligible,
            normalized_country=geo.normalized_country,
        )

    if prefs.prefers_backend is True and _FRONTEND_ONLY.search(title):
        blob = f"{title} {(adjusted.description_snippet or '').lower()}"
        if "backend" not in blob and "full stack" not in blob and "fullstack" not in blob:
            return RankedDiscoveryCandidate(
                raw=adjusted,
                filtered=True,
                filter_reason="FRONTEND_ONLY",
                us_work_eligible=geo.us_work_eligible,
                normalized_country=geo.normalized_country,
            )

    if prefs.prefers_software_development is True and not _looks_like_ic_dev_role(
        title, prefs
    ):
        return RankedDiscoveryCandidate(
            raw=adjusted,
            filtered=True,
            filter_reason="NON_TARGET_ROLE_FAMILY",
            us_work_eligible=geo.us_work_eligible,
            normalized_country=geo.normalized_country,
        )

    job = raw_to_normalized_job(adjusted)
    hf = apply_hard_filters(profile, job)
    if not hf.passed:
        code = hf.rejection_reasons[0].code if hf.rejection_reasons else "HARD_FILTER"
        return RankedDiscoveryCandidate(
            raw=adjusted,
            filtered=True,
            filter_reason=code,
            us_work_eligible=geo.us_work_eligible,
            normalized_country=geo.normalized_country,
        )

    return RankedDiscoveryCandidate(
        raw=adjusted,
        filtered=False,
        us_work_eligible=geo.us_work_eligible,
        normalized_country=geo.normalized_country,
    )


def _is_management_track(title: str, prefs: JobPreferences) -> bool:
    """Filter people-management titles unless the candidate targets them."""
    targets = " ".join(prefs.target_roles or []).lower()
    acceptable = " ".join(prefs.acceptable_roles or []).lower()
    allowed = f"{targets} {acceptable}"
    if any(tok in allowed for tok in ("manager", "director", "head of", "vp ")):
        return False
    return any(re.search(p, title, re.I) for p in _MANAGEMENT_TITLE_PATTERNS)


def _looks_like_ic_dev_role(title: str, prefs: JobPreferences) -> bool:
    """Cheap role-family gate — keyword families only, not semantic Scout judgment."""
    if _IC_DEV_TITLE.search(title):
        return True
    # Target / acceptable role substrings
    for role in list(prefs.target_roles or []) + list(prefs.acceptable_roles or []):
        role_l = role.lower().strip()
        if role_l and (role_l in title or any(
            tok in title for tok in role_l.split() if len(tok) > 3
        )):
            # Avoid matching bare "engineer" from "Sales Engineer" (already unrelated)
            return True
    return False

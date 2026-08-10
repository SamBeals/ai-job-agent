"""Cheap deterministic Discovery pre-filters (not Scout)."""

from __future__ import annotations

import re

from app.schemas.candidate import CandidateProfile
from app.schemas.discovery import RankedDiscoveryCandidate, RawDiscoveryResult
from app.schemas.job_posting import NormalizedJob
from app.agents.scout.hard_filters import apply_hard_filters


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
    r"\bscrum\s+master\b",
    r"\bproject\s+manager\b",
    r"\brecruiter\b",
    r"\bcustomer\s+success\b",
]

_FRONTEND_ONLY = re.compile(
    r"\b(frontend|front[- ]end)\b.*\b(engineer|developer)\b|\b(engineer|developer)\b.*\b(frontend|front[- ]end)\b",
    re.I,
)
_INTERNSHIP = re.compile(r"\b(intern|internship|co[- ]?op)\b", re.I)


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
    """Reject obvious mismatches; unknown salary must not reject."""
    # Hourly / ambiguous compensation: do not invent annual; skip salary hard reject
    if raw.salary_period and raw.salary_period.lower() in {"hour", "hourly", "hr"}:
        # Treat as unknown for filtering — do not convert
        adjusted = raw.model_copy(update={"salary_min": None, "salary_max": None})
    else:
        adjusted = raw

    title = adjusted.title.lower()
    prefs = profile.preferences

    # Internship when seeking experienced roles
    if _INTERNSHIP.search(title):
        targets = " ".join(prefs.target_roles or []).lower()
        if "intern" not in targets:
            return RankedDiscoveryCandidate(
                raw=adjusted,
                filtered=True,
                filter_reason="INTERNSHIP_NOT_TARGETED",
            )

    for pattern in _UNRELATED_TITLE_PATTERNS:
        if re.search(pattern, title, re.I):
            return RankedDiscoveryCandidate(
                raw=adjusted,
                filtered=True,
                filter_reason="UNRELATED_ROLE",
            )

    # Frontend-only when prefers_backend
    if prefs.prefers_backend is True and _FRONTEND_ONLY.search(title):
        blob = f"{title} {(adjusted.description_snippet or '').lower()}"
        if "backend" not in blob and "full stack" not in blob and "fullstack" not in blob:
            return RankedDiscoveryCandidate(
                raw=adjusted,
                filtered=True,
                filter_reason="FRONTEND_ONLY",
            )

    # Reuse Scout hard filters for salary / excluded roles / contract / etc.
    job = raw_to_normalized_job(adjusted)
    # Clear salary for hourly already handled; hard filters treat missing as unknown
    hf = apply_hard_filters(profile, job)
    if not hf.passed:
        code = hf.rejection_reasons[0].code if hf.rejection_reasons else "HARD_FILTER"
        return RankedDiscoveryCandidate(
            raw=adjusted,
            filtered=True,
            filter_reason=code,
        )

    return RankedDiscoveryCandidate(raw=adjusted, filtered=False)

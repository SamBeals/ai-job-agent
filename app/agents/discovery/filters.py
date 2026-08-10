"""Cheap deterministic Discovery pre-filters (not Scout)."""

from __future__ import annotations

import re

from app.agents.discovery.geography import assess_geography, requires_us_employment
from app.agents.discovery.viability import assess_viability, infer_arrangement
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
    r"\bbusiness\s+development\b",
]

# \"Developer\" in non-software contexts must not count as Software Developer
_NON_SOFTWARE_DEVELOPER_CONTEXT = [
    r"\bdeveloper\s+relations\b",
    r"\bdeveloper\s+marketing\b",
    r"\bdeveloper\s+gtm\b",
    r"\bdeveloper\s+sales\b",
    r"\bdeveloper\s+advocate\b",
    r"\bdeveloper\s+evangelist\b",
    r"\bdeveloper\s+experience\b",
    r"\bdevrel\b",
    r"\bgtm\b.*\bfinance\b",
    r"\bfinance\b.*\bgtm\b",
    r"\bdeveloper\s+gtm\s+finance\b",
    r"\bdirector,?\s+developer\b",
    r"\bmarketing\b.*\bdeveloper\b",
    r"\bdeveloper\b.*\bmarketing\b",
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
    # Director in non-engineering contexts (e.g. Director, Developer GTM Finance)
    r"^\s*director\b",
    r"\bdirector,",
]

_FRONTEND_ONLY = re.compile(
    r"\b(frontend|front[- ]end)\b.*\b(engineer|developer)\b|"
    r"\b(engineer|developer)\b.*\b(frontend|front[- ]end)\b",
    re.I,
)
_INTERNSHIP = re.compile(r"\b(intern|internship|co[- ]?op)\b", re.I)

# Legitimate IC software titles — bare \"developer\" alone is NOT enough
_IC_DEV_TITLE = re.compile(
    r"\b("
    r"software\s+engineer|software\s+developer|"
    r"backend(\s+software)?\s+(engineer|developer)|back[- ]end(\s+software)?\s+(engineer|developer)|"
    r"full[- ]?stack(\s+software)?\s+(engineer|developer)|"
    r"platform\s+engineer|application\s+(engineer|developer)|"
    r"java\s+(software\s+)?(engineer|developer)|"
    r"distributed\s+systems\s+engineer|"
    r"forward\s+deployed\s+engineer|"
    r"site\s+reliability(\s+engineer)?|\bsre\b|"
    r"devops\s+engineer|"
    r"api\s+engineer"
    r")\b",
    re.I,
)

_LANGUAGE_SPEAKER = re.compile(
    r"\b(hebrew|spanish|french|german|japanese|mandarin|chinese|korean|"
    r"portuguese|arabic|russian|hindi|italian)\s+speaker\b",
    re.I,
)
_LANGUAGE_COMMA = re.compile(
    r",\s*(hebrew|spanish|french|german|japanese|mandarin|chinese|korean|"
    r"portuguese|arabic|russian|hindi|italian)\b",
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
    """Reject obvious mismatches; unknown salary must not reject."""
    if raw.salary_period and raw.salary_period.lower() in {"hour", "hourly", "hr"}:
        adjusted = raw.model_copy(update={"salary_min": None, "salary_max": None})
    else:
        adjusted = raw

    prefs = profile.preferences
    location_for_geo = _location_for_geography(adjusted)
    arrangement = infer_arrangement(location_for_geo, adjusted.work_arrangement)
    geo = assess_geography(
        location_for_geo,
        work_arrangement=arrangement,
    )
    viability = assess_viability(
        prefs,
        location_text=location_for_geo,
        work_arrangement=arrangement or adjusted.work_arrangement,
        geo=geo,
    )
    adjusted = adjusted.model_copy(
        update={
            "normalized_country": viability.normalized_country,
            "us_work_eligible": viability.us_work_eligible,
            "work_arrangement": arrangement or adjusted.work_arrangement,
            "location_text": adjusted.location_text or location_for_geo,
        }
    )

    info_codes: list[str] = list(viability.reason_codes)

    # Geographic hard rejects (foreign + nonlocal physical when relocation not allowed)
    if viability.filter_reason:
        return RankedDiscoveryCandidate(
            raw=adjusted,
            filtered=True,
            filter_reason=viability.filter_reason,
            us_work_eligible=viability.us_work_eligible,
            normalized_country=viability.normalized_country,
            reason_codes=info_codes,
        )

    # Explicit foreign hard-reject when US employment is required (belt + suspenders)
    if requires_us_employment(prefs) and viability.us_work_eligible is False:
        return RankedDiscoveryCandidate(
            raw=adjusted,
            filtered=True,
            filter_reason="FOREIGN_LOCATION",
            us_work_eligible=False,
            normalized_country=viability.normalized_country,
            reason_codes=info_codes,
        )

    title = adjusted.title.lower()

    if _INTERNSHIP.search(title):
        targets = " ".join(prefs.target_roles or []).lower()
        if "intern" not in targets:
            return RankedDiscoveryCandidate(
                raw=adjusted,
                filtered=True,
                filter_reason="INTERNSHIP_NOT_TARGETED",
                us_work_eligible=viability.us_work_eligible,
                normalized_country=viability.normalized_country,
            )

    for pattern in _NON_SOFTWARE_DEVELOPER_CONTEXT:
        if re.search(pattern, title, re.I):
            return RankedDiscoveryCandidate(
                raw=adjusted,
                filtered=True,
                filter_reason="NON_SOFTWARE_DEVELOPER_CONTEXT",
                us_work_eligible=viability.us_work_eligible,
                normalized_country=viability.normalized_country,
                reason_codes=["NON_SOFTWARE_DEVELOPER_CONTEXT"],
            )

    for pattern in _UNRELATED_TITLE_PATTERNS:
        if re.search(pattern, title, re.I):
            return RankedDiscoveryCandidate(
                raw=adjusted,
                filtered=True,
                filter_reason="UNRELATED_ROLE",
                us_work_eligible=viability.us_work_eligible,
                normalized_country=viability.normalized_country,
            )

    if _is_management_track(title, prefs):
        return RankedDiscoveryCandidate(
            raw=adjusted,
            filtered=True,
            filter_reason="MANAGEMENT_ROLE",
            us_work_eligible=viability.us_work_eligible,
            normalized_country=viability.normalized_country,
        )

    # Mandatory language in title — reject when candidate inventory has no evidence
    lang = _extract_mandatory_language(title)
    if lang:
        info_codes.append("MANDATORY_LANGUAGE_SIGNAL")
        if not _candidate_has_language(profile, lang):
            return RankedDiscoveryCandidate(
                raw=adjusted,
                filtered=True,
                filter_reason="MANDATORY_LANGUAGE_UNMET",
                us_work_eligible=viability.us_work_eligible,
                normalized_country=viability.normalized_country,
                reason_codes=info_codes,
            )

    if prefs.prefers_backend is True and _FRONTEND_ONLY.search(title):
        blob = f"{title} {(adjusted.description_snippet or '').lower()}"
        if "backend" not in blob and "full stack" not in blob and "fullstack" not in blob:
            return RankedDiscoveryCandidate(
                raw=adjusted,
                filtered=True,
                filter_reason="FRONTEND_ONLY",
                us_work_eligible=viability.us_work_eligible,
                normalized_country=viability.normalized_country,
            )

    if prefs.prefers_software_development is True and not _looks_like_ic_dev_role(
        title, prefs
    ):
        return RankedDiscoveryCandidate(
            raw=adjusted,
            filtered=True,
            filter_reason="NON_TARGET_ROLE_FAMILY",
            us_work_eligible=viability.us_work_eligible,
            normalized_country=viability.normalized_country,
        )

    job = raw_to_normalized_job(adjusted)
    hf = apply_hard_filters(profile, job)
    if not hf.passed:
        code = hf.rejection_reasons[0].code if hf.rejection_reasons else "HARD_FILTER"
        return RankedDiscoveryCandidate(
            raw=adjusted,
            filtered=True,
            filter_reason=code,
            us_work_eligible=viability.us_work_eligible,
            normalized_country=viability.normalized_country,
        )

    return RankedDiscoveryCandidate(
        raw=adjusted,
        filtered=False,
        us_work_eligible=viability.us_work_eligible,
        normalized_country=viability.normalized_country,
        reason_codes=info_codes,
    )


def _location_for_geography(raw: RawDiscoveryResult) -> str | None:
    """Prefer location_text; fall back to provider country metadata when needed."""
    loc = (raw.location_text or "").strip()
    meta = raw.raw_metadata or {}
    country = meta.get("country") or meta.get("normalized_country")
    country_s = str(country).strip() if country else ""
    if not loc or loc.lower() in {"n/a", "na", "none", "null", "-", "unknown"}:
        return country_s or loc or None
    if country_s and country_s.lower() not in loc.lower():
        # Keep city/region text primary; country alone already covered when loc empty
        return loc
    return loc or None



def _is_management_track(title: str, prefs: JobPreferences) -> bool:
    """Filter people-management titles unless the candidate targets them."""
    targets = " ".join(prefs.target_roles or []).lower()
    acceptable = " ".join(prefs.acceptable_roles or []).lower()
    allowed = f"{targets} {acceptable}"
    if any(tok in allowed for tok in ("manager", "director", "head of", "vp ")):
        return False
    # Allow "Director of Engineering" only if targeted; patterns still match otherwise
    return any(re.search(p, title, re.I) for p in _MANAGEMENT_TITLE_PATTERNS)


def _looks_like_ic_dev_role(title: str, prefs: JobPreferences) -> bool:
    """Cheap role-family gate — phrase families only, not bare 'developer'."""
    if _IC_DEV_TITLE.search(title):
        return True
    for role in list(prefs.target_roles or []) + list(prefs.acceptable_roles or []):
        role_l = role.lower().strip()
        if len(role_l) < 8:
            continue
        if role_l in title:
            return True
    return False


def _extract_mandatory_language(title: str) -> str | None:
    m = _LANGUAGE_SPEAKER.search(title) or _LANGUAGE_COMMA.search(title)
    if not m:
        return None
    return m.group(1).lower()


def _candidate_has_language(profile: CandidateProfile, language: str) -> bool:
    """True only when verified skill inventory lists the language (or close alias)."""
    aliases = {
        "chinese": {"chinese", "mandarin", "cantonese"},
        "mandarin": {"mandarin", "chinese"},
    }
    wanted = aliases.get(language, {language})
    for skill in profile.skills.languages:
        name = (skill.name or "").lower()
        if any(w in name for w in wanted):
            return True
    # Also scan notes / professional summary lightly? Prefer inventory only.
    return False

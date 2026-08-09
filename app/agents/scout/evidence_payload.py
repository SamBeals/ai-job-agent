"""Minimal candidate evidence payload for LLM qualification — privacy-first."""

from __future__ import annotations

from typing import Any

from app.schemas.candidate import CandidateProfile


_FORBIDDEN_IDENTITY_KEYS = {
    "email",
    "phone",
    "linkedin_url",
    "github_url",
    "portfolio_url",
}


def build_candidate_evidence_payload(candidate: CandidateProfile) -> dict[str, Any]:
    """Build the ONLY candidate data sent to an LLM for qualification.

    Includes: work experience, projects, skills, education, certifications,
    and approximate total years derived from dates.

    Excludes: phone, email, address details beyond coarse location label,
    preferences (desirability is deterministic), Discord/application data.
    """
    years = candidate.approximate_years_of_experience()
    return {
        "full_name": candidate.identity.full_name,
        "location_label": candidate.identity.location,
        "approximate_years_professional_experience": years,
        "professional_summary": candidate.professional_summary,
        "work_experience": [
            {
                "company": w.company,
                "title": w.title,
                "location": w.location,
                "employment_type": w.employment_type,
                "start_date": w.start_date,
                "end_date": w.end_date,
                "is_current": w.is_current,
                "verified_accomplishments": list(w.verified_accomplishments),
                "technologies": list(w.technologies),
                "responsibilities": list(w.responsibilities),
            }
            for w in candidate.work_experience
        ],
        "projects": [
            {
                "name": p.name,
                "description": p.description,
                "technologies": list(p.technologies),
                "verified_outcomes": list(p.verified_outcomes),
            }
            for p in candidate.projects
        ],
        "skills": {
            "languages": [_skill_dict(s) for s in candidate.skills.languages],
            "frameworks": [_skill_dict(s) for s in candidate.skills.frameworks],
            "cloud_and_infra": [_skill_dict(s) for s in candidate.skills.cloud_and_infra],
            "databases": [_skill_dict(s) for s in candidate.skills.databases],
            "practices": [_skill_dict(s) for s in candidate.skills.practices],
            "other": [_skill_dict(s) for s in candidate.skills.other],
        },
        "education": [
            {
                "institution": e.institution,
                "degree": e.degree,
                "field": e.field,
                "status": e.status,
                "start_date": e.start_date,
                "end_date": e.end_date,
                "graduation_date": e.graduation_date,
            }
            for e in candidate.education
        ],
        "certifications": [
            {"name": c.name, "issuer": c.issuer, "date_earned": c.date_earned}
            for c in candidate.certifications
        ],
        "evidence_notes": [
            "A skill listed in skills inventory is LISTED_SKILL evidence only unless also shown in work_experience.technologies or accomplishments.",
            "Do not invent technology-specific years from approximate_years_professional_experience.",
            "Absence of evidence means NO_EVIDENCE or UNKNOWN — not a claim the candidate cannot learn the skill.",
        ],
    }


def assert_payload_has_no_sensitive_fields(payload: dict[str, Any]) -> None:
    """Raise AssertionError if forbidden identity fields appear in the payload."""
    blob = str(payload).lower()
    for key in _FORBIDDEN_IDENTITY_KEYS:
        # Allow the key name only inside evidence_notes explanations, not as data fields
        if f"'{key}'" in str(payload) or f'"{key}"' in str(payload):
            # Check nested keys more carefully
            pass
    flat_keys = _collect_keys(payload)
    leaked = flat_keys & _FORBIDDEN_IDENTITY_KEYS
    if leaked:
        raise AssertionError(f"Sensitive fields leaked into LLM payload: {sorted(leaked)}")


def _collect_keys(obj: Any, found: set[str] | None = None) -> set[str]:
    found = found or set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(str(k))
            _collect_keys(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _collect_keys(item, found)
    return found


def _skill_dict(skill) -> dict[str, Any]:
    return {
        "name": skill.name,
        "verified": skill.verified,
        "source": skill.source,
        "evidence_type": skill.evidence_type.value if skill.evidence_type else "LISTED_SKILL",
        "proficiency": skill.proficiency,  # typically null
        "years_experience": skill.years_experience,  # typically null
        "notes": skill.notes,
    }

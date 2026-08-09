"""Lightweight evaluation fingerprint for future cache keys (not a cache yet)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.agents.scout.evidence_payload import build_candidate_evidence_payload
from app.schemas.candidate import CandidateProfile
from app.schemas.job_posting import NormalizedJob


def compute_evaluation_fingerprint(
    *,
    job: NormalizedJob,
    candidate: CandidateProfile,
    prompt_version: str,
    model: str | None,
    provider: str,
) -> str:
    """Hash of job content + candidate evidence + prompt/model/provider.

    Intended for future identical-evaluation cache lookups. Does not skip
    persistence of evaluation history.
    """
    payload: dict[str, Any] = {
        "job": {
            "title": job.title,
            "company": job.company,
            "description": job.description,
            "responsibilities": job.responsibilities,
            "required_skills": job.required_skills,
            "preferred_skills": job.preferred_skills,
            "required_years_experience": job.required_years_experience,
            "education_requirements": job.education_requirements,
            "remote_status": job.remote_status,
            "location": job.location,
            "salary_min": job.salary_min,
            "salary_max": job.salary_max,
        },
        "candidate_evidence": build_candidate_evidence_payload(candidate),
        "prompt_version": prompt_version,
        "model": model,
        "provider": provider,
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

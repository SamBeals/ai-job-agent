"""Convert raw extraction into validated NormalizedJob."""

from __future__ import annotations

from datetime import datetime, timezone

from app.agents.scout.ingestion.html_extract import RawExtractedJob
from app.agents.scout.ingestion.models import (
    ExtractionConfidence,
    ExtractionError,
    ExtractionMethod,
    ExtractionResult,
    InputSource,
)
from app.schemas.job_posting import NormalizedJob


def normalize_extracted(
    raw: RawExtractedJob,
    *,
    input_source: InputSource,
    source_url: str | None = None,
    source_label: str | None = None,
    partial_content: bool = False,
    extractor_version: str = "2a.5.1",
) -> ExtractionResult:
    warnings = list(raw.warnings)
    title = (raw.title or "").strip()
    company = (raw.company or "").strip()

    if not title:
        raise ExtractionError(
            "Scout retrieved content but couldn't confidently identify a job posting. "
            "Paste the job description instead."
        )
    if not company:
        company = "Unknown Company"
        warnings.append("Company name was not confidently identified.")

    method: ExtractionMethod
    try:
        method = ExtractionMethod(raw.method)
    except ValueError:
        method = ExtractionMethod.HTML

    confidence = _confidence(raw, method, partial_content)
    if partial_content:
        warnings.append(
            "Evaluation may be based on partial job content (Discord length limit). "
            "Prefer the CLI for full postings."
        )
        if confidence == ExtractionConfidence.HIGH:
            confidence = ExtractionConfidence.MEDIUM

    job = NormalizedJob(
        external_id=_external_id(input_source, source_url, title, company),
        source=source_label or input_source.value.lower(),
        source_url=source_url,
        company=company,
        title=title,
        location=raw.location,
        remote_status=raw.remote_status,
        employment_type=raw.employment_type,
        salary_min=raw.salary_min,
        salary_max=raw.salary_max,
        salary_currency=raw.salary_currency,
        description=raw.description,
        responsibilities=list(raw.responsibilities or []),
        required_skills=list(raw.required_skills or []),
        preferred_skills=list(raw.preferred_skills or []),
        required_years_experience=raw.required_years_experience,
        education_requirements=list(raw.education_requirements or []),
        seniority=raw.seniority,
        discovered_at=datetime.now(timezone.utc),
    )

    return ExtractionResult(
        normalized_job=job,
        input_source=input_source,
        extraction_method=method,
        extraction_confidence=confidence,
        warnings=warnings,
        partial_content=partial_content,
        extractor_version=extractor_version,
        original_url=source_url,
    )


def _confidence(
    raw: RawExtractedJob,
    method: ExtractionMethod,
    partial: bool,
) -> ExtractionConfidence:
    score = 0
    if raw.title:
        score += 2
    if raw.company and raw.company != "Unknown Company":
        score += 2
    if raw.description and len(raw.description) > 200:
        score += 2
    if raw.location or raw.remote_status:
        score += 1
    if raw.salary_min or raw.salary_max:
        score += 1
    if method == ExtractionMethod.JSON_LD:
        score += 2
    if method == ExtractionMethod.FIXTURE:
        return ExtractionConfidence.HIGH
    if partial:
        score -= 2
    if score >= 7:
        return ExtractionConfidence.HIGH
    if score >= 4:
        return ExtractionConfidence.MEDIUM
    return ExtractionConfidence.LOW


def _external_id(source: InputSource, url: str | None, title: str, company: str) -> str:
    if url:
        return f"{source.value.lower()}:{url}"
    slug = f"{company}:{title}".lower().replace(" ", "-")[:180]
    return f"{source.value.lower()}:{slug}"

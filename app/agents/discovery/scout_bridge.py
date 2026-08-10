"""SCOUT THIS — route a DiscoveryResult through existing ingestion + ScoutPipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agents.scout.ingestion.models import IngestionError
from app.agents.scout.ingestion.service import JobIngestionService
from app.agents.scout.pipeline import ScoutPipeline
from app.agents.scout.profile_loader import load_candidate_profile
from app.config import Settings, get_settings
from app.models.discovery import DiscoveryResult
from app.models.job import Job
from app.schemas.discovery import DiscoveryResultStatus
from app.schemas.evaluation import ScoutEvaluation
from app.schemas.job_posting import NormalizedJob

logger = logging.getLogger(__name__)

MIN_DESCRIPTION_CHARS = 280


@dataclass
class ScoutFromDiscoveryResult:
    ok: bool
    message: str
    job: Job | None = None
    evaluation: ScoutEvaluation | None = None
    used_structured_content: bool = False
    needs_paste_fallback: bool = False


def scout_discovery_result(
    session: Session,
    discovery_result_id: int,
    *,
    settings: Settings | None = None,
) -> ScoutFromDiscoveryResult:
    """Invoke existing Scout path for a DiscoveryResult. Never creates Approval."""
    settings = settings or get_settings()
    row = session.get(DiscoveryResult, discovery_result_id)
    if row is None:
        return ScoutFromDiscoveryResult(ok=False, message="Discovery result not found.")

    logger.info(
        "discovery_scout_requested result_id=%s provider=%s",
        row.id,
        row.provider,
    )
    row.status = DiscoveryResultStatus.SCOUT_REQUESTED.value
    session.flush()

    ingestion = JobIngestionService(settings)
    profile = load_candidate_profile(settings.candidate_profile_path)
    used_structured = False
    extraction = None

    structured = (row.description_full or row.description_snippet or "").strip()
    url = row.canonical_url or row.job_url

    if len(structured) >= MIN_DESCRIPTION_CHARS:
        used_structured = True
        extraction = ingestion.ingest_text(
            structured,
            title=row.title,
            company=row.company,
            source_url=url,
            partial_content=len(structured) < 800,
        )
        # Prefer provider metadata for arrangement/salary when present
        job = extraction.normalized_job
        extraction.normalized_job = _merge_discovery_metadata(job, row)
    elif url:
        try:
            extraction = ingestion.ingest_url(url)
            extraction.normalized_job = _merge_discovery_metadata(
                extraction.normalized_job, row
            )
        except IngestionError as exc:
            if len(structured) >= 80:
                used_structured = True
                extraction = ingestion.ingest_text(
                    structured,
                    title=row.title,
                    company=row.company,
                    source_url=url,
                    partial_content=True,
                )
                extraction.normalized_job = _merge_discovery_metadata(
                    extraction.normalized_job, row
                )
            else:
                row.status = DiscoveryResultStatus.SCOUT_REQUESTED.value
                session.flush()
                return ScoutFromDiscoveryResult(
                    ok=False,
                    needs_paste_fallback=True,
                    message=(
                        "Scout found the opportunity, but the source does not expose "
                        "enough job-description content for a reliable evaluation.\n\n"
                        "Open the posting and use PASTE JOB if you'd like Scout to evaluate it."
                    ),
                )
    else:
        return ScoutFromDiscoveryResult(
            ok=False,
            needs_paste_fallback=True,
            message=(
                "Scout found the opportunity, but the source does not expose "
                "enough job-description content for a reliable evaluation.\n\n"
                "Open the posting and use PASTE JOB if you'd like Scout to evaluate it."
            ),
        )

    assert extraction is not None
    desc = (extraction.normalized_job.description or "").strip()
    if len(desc) < 80:
        return ScoutFromDiscoveryResult(
            ok=False,
            needs_paste_fallback=True,
            used_structured_content=used_structured,
            message=(
                "Scout found the opportunity, but the source does not expose "
                "enough job-description content for a reliable evaluation.\n\n"
                "Open the posting and use PASTE JOB if you'd like Scout to evaluate it."
            ),
        )

    pipeline = ScoutPipeline(settings=settings, session=session)
    result = pipeline.evaluate(
        extraction.normalized_job,
        profile,
        persist=True,
        create_job_record=True,
        source_content_partial=extraction.partial_content,
        extraction_confidence=extraction.extraction_confidence.value,
    )
    if result.job is not None:
        row.job_id = result.job.id
    row.status = DiscoveryResultStatus.SCOUTED.value
    session.flush()

    return ScoutFromDiscoveryResult(
        ok=True,
        message="Scout evaluation complete.",
        job=result.job,
        evaluation=result.evaluation,
        used_structured_content=used_structured,
    )


def dismiss_discovery_result(session: Session, discovery_result_id: int) -> DiscoveryResult:
    row = session.get(DiscoveryResult, discovery_result_id)
    if row is None:
        raise ValueError("Discovery result not found")
    row.status = DiscoveryResultStatus.DISMISSED.value
    session.flush()
    logger.info("discovery_result_dismissed result_id=%s", row.id)
    return row


def _merge_discovery_metadata(job: NormalizedJob, row: DiscoveryResult) -> NormalizedJob:
    data = job.model_dump()
    if not data.get("location") and row.location:
        data["location"] = row.location
    if not data.get("remote_status") and row.work_arrangement:
        data["remote_status"] = row.work_arrangement
    if data.get("salary_min") is None and row.salary_min is not None:
        data["salary_min"] = row.salary_min
    if data.get("salary_max") is None and row.salary_max is not None:
        data["salary_max"] = row.salary_max
    if not data.get("source_url"):
        data["source_url"] = row.canonical_url or row.job_url
    data["source"] = data.get("source") or f"discovery:{row.provider}"
    data["external_id"] = data.get("external_id") or f"{row.provider}:{row.external_id}"
    data["company"] = data.get("company") or row.company
    data["title"] = data.get("title") or row.title
    return NormalizedJob.model_validate(data)

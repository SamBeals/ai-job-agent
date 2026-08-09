"""High-level job ingestion — URL / text / fixture → ExtractionResult."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.agents.scout.ingestion.html_extract import extract_from_html
from app.agents.scout.ingestion.models import (
    ExtractionError,
    ExtractionMethod,
    ExtractionResult,
    IngestionError,
    InputSource,
)
from app.agents.scout.ingestion.normalizer import normalize_extracted
from app.agents.scout.ingestion.text_parser import extract_from_text
from app.agents.scout.ingestion.url_fetch import fetch_job_page
from app.config import Settings, get_settings
from app.schemas.job_posting import NormalizedJob

logger = logging.getLogger(__name__)

DISCORD_DESCRIPTION_MAX = 4000

FIXTURE_CATALOG: dict[str, tuple[str, str]] = {
    "a_strong_backend": ("fixture_a_strong_backend.json", "Strong Backend Match"),
    "b_ml_research": ("fixture_b_ml_research.json", "ML Research Mismatch"),
    "c_onsite": ("fixture_c_onsite_undesirable.json", "Onsite Preference Test"),
    "d_missing": ("fixture_d_missing_info.json", "Missing Information"),
    "e_keyword": ("fixture_e_keyword_trap.json", "Keyword Trap"),
    "f_preferred": ("fixture_f_preferred_gap.json", "Preferred Skill Gap"),
    "g_calibration_se": (
        "fixture_g_calibration_software_engineer.json",
        "Calibration Software Engineer (remote)",
    ),
}


class JobIngestionService:
    """Fetch/extract/normalize only — evaluation stays in ScoutPipeline."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def ingest_fixture(self, fixture_key: str, *, fixtures_dir: str | Path | None = None) -> ExtractionResult:
        logger.info("job_ingestion_started source=fixture key=%s", fixture_key)
        key = fixture_key.strip().lower()
        if key not in FIXTURE_CATALOG:
            raise IngestionError(
                "Unknown fixture. Choose one of: " + ", ".join(FIXTURE_CATALOG),
                code="UNKNOWN_FIXTURE",
            )
        filename, _label = FIXTURE_CATALOG[key]
        root = Path(fixtures_dir or "data/fixtures/scout")
        path = root / filename
        if not path.exists():
            raise IngestionError(f"Fixture file missing: {path}", code="FIXTURE_MISSING")
        data = json.loads(path.read_text(encoding="utf-8"))
        job = NormalizedJob.model_validate(data)
        job.source = job.source or "fixture"
        from app.agents.scout.ingestion.html_extract import RawExtractedJob

        raw = RawExtractedJob(
            title=job.title,
            company=job.company,
            location=job.location,
            remote_status=job.remote_status,
            employment_type=job.employment_type,
            description=job.description,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            required_skills=list(job.required_skills),
            preferred_skills=list(job.preferred_skills),
            responsibilities=list(job.responsibilities),
            required_years_experience=job.required_years_experience,
            education_requirements=list(job.education_requirements),
            seniority=job.seniority,
            method="FIXTURE",
        )
        result = normalize_extracted(
            raw,
            input_source=InputSource.FIXTURE,
            source_url=job.source_url,
            source_label="fixture",
            extractor_version=self.settings.ingestion_extractor_version,
        )
        # Preserve fixture NormalizedJob fields exactly
        result.normalized_job = job
        result.extraction_method = ExtractionMethod.FIXTURE
        logger.info("normalization_success method=FIXTURE")
        return result

    def ingest_text(
        self,
        text: str,
        *,
        title: str | None = None,
        company: str | None = None,
        source_url: str | None = None,
        partial_content: bool = False,
    ) -> ExtractionResult:
        logger.info("job_ingestion_started source=text")
        if not (text or "").strip():
            raise ExtractionError("Job description text is empty.")
        # Discord modal hard cap — refuse silent truncation pretenses
        if len(text) > DISCORD_DESCRIPTION_MAX and partial_content is False:
            # CLI can send longer; only Discord sets partial_content when at cap
            pass
        raw = extract_from_text(text, title=title, company=company, source_url=source_url)
        result = normalize_extracted(
            raw,
            input_source=InputSource.TEXT,
            source_url=source_url,
            source_label="text",
            partial_content=partial_content,
            extractor_version=self.settings.ingestion_extractor_version,
        )
        logger.info(
            "normalization_success method=%s confidence=%s",
            result.extraction_method.value,
            result.extraction_confidence.value,
        )
        return result

    def ingest_url(self, url: str, *, http_client=None) -> ExtractionResult:
        logger.info("job_ingestion_started source=url")
        page = fetch_job_page(url, settings=self.settings, client=http_client)
        raw = extract_from_html(page.text, page_url=page.final_url)
        if not raw.title and not (raw.description and len(raw.description) > 80):
            raise ExtractionError(
                "Scout retrieved the page but couldn't confidently identify a job posting. "
                "Paste the job description instead."
            )
        result = normalize_extracted(
            raw,
            input_source=InputSource.URL,
            source_url=page.final_url,
            source_label="url",
            extractor_version=self.settings.ingestion_extractor_version,
        )
        logger.info(
            "extraction_method=%s normalization_success confidence=%s",
            result.extraction_method.value,
            result.extraction_confidence.value,
        )
        return result

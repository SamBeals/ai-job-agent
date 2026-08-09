"""Ingestion → Scout pipeline integration and authorization invariants."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.agents.scout.ingestion.service import JobIngestionService
from app.agents.scout.ingestion.url_fetch import fetch_job_page
from app.agents.scout.llm.mock import MockLLMClient
from app.agents.scout.pipeline import ScoutPipeline
from app.agents.scout.profile_loader import load_candidate_profile
from app.agents.scout.scoring import ScoutThresholds
from app.discord.scout_views import ScoutIngestView
from app.models.job import JobStatus
from app.schemas.evaluation import Recommendation
from app.services.approval_service import ApprovalService
from pathlib import Path


PROFILE = Path(__file__).resolve().parents[1] / "data/fixtures/profiles/test_office_backend_prefs.json"


def test_url_ingested_job_reaches_pipeline(session) -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"JobPosting","title":"Senior Java Backend Engineer",
     "hiringOrganization":{"name":"Desert Systems"},
     "description":"Java Spring Boot REST APIs AWS Kubernetes SQL",
     "jobLocation":{"address":{"addressLocality":"Chandler","addressRegion":"AZ"}},
     "baseSalary":{"currency":"USD","value":{"minValue":140000,"maxValue":165000,"unitText":"YEAR"}}}
    </script></head><body></body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    with patch("app.agents.scout.ingestion.url_safety._assert_hostname_public"):
        with patch("app.agents.scout.ingestion.url_fetch.validate_public_http_url", side_effect=lambda u, resolve=True: u):
            with patch("app.agents.scout.ingestion.url_fetch.assert_redirect_target_safe"):
                extraction = JobIngestionService().ingest_url(
                    "https://example.com/jobs/1",
                    http_client=client,
                )

    candidate = load_candidate_profile(PROFILE)
    pipeline = ScoutPipeline(
        llm_client=MockLLMClient(),
        thresholds=ScoutThresholds(),
        session=session,
    )
    result = pipeline.evaluate(
        extraction.normalized_job,
        candidate,
        persist=True,
        create_job_record=True,
    )
    assert result.evaluation.qualification_score >= 50
    assert result.job is not None
    assert result.job.status_enum != JobStatus.APPROVED


def test_text_ingested_job_reaches_pipeline(session) -> None:
    extraction = JobIngestionService().ingest_text(
        """
        Senior Backend Software Engineer
        Cloud Harbor
        Remote - US
        Salary: $150,000-$175,000

        Requirements:
        - Java
        - Spring Boot
        - REST APIs
        - AWS
        - Kubernetes
        - SQL
        """
    )
    candidate = load_candidate_profile(PROFILE)
    pipeline = ScoutPipeline(
        llm_client=MockLLMClient(),
        thresholds=ScoutThresholds(),
        session=session,
    )
    result = pipeline.evaluate(
        extraction.normalized_job,
        candidate,
        persist=True,
        create_job_record=True,
    )
    assert result.evaluation.desirability_score >= 70
    assert result.job is not None


def test_salary_hard_rule_still_works_on_ingested_text(session) -> None:
    extraction = JobIngestionService().ingest_text(
        """
        Java Backend Engineer
        Bargain Soft
        Chandler, AZ
        Hybrid
        Salary: $95,000-$105,000
        Requirements:
        - Java
        - Spring Boot
        - REST APIs
        - AWS
        - Kubernetes
        - SQL
        """
    )
    candidate = load_candidate_profile(PROFILE)
    result = ScoutPipeline(
        llm_client=MockLLMClient(),
        thresholds=ScoutThresholds(),
        session=session,
    ).evaluate(extraction.normalized_job, candidate, persist=True, create_job_record=True)
    assert result.evaluation.recommendation == Recommendation.HARD_REJECT


def test_ingestion_cannot_approve(session, approval_service: ApprovalService) -> None:
    extraction = JobIngestionService().ingest_fixture("a_strong_backend")
    candidate = load_candidate_profile(PROFILE)
    result = ScoutPipeline(
        llm_client=MockLLMClient(),
        thresholds=ScoutThresholds(),
        session=session,
    ).evaluate(extraction.normalized_job, candidate, persist=True, create_job_record=True)
    session.commit()
    assert result.job is not None
    assert approval_service.get_approval_for_job(result.job.id) is None
    assert approval_service.can_enter_application_pipeline(result.job.id) is False


def test_strong_recommend_remains_unauthorized(session, approval_service: ApprovalService) -> None:
    extraction = JobIngestionService().ingest_text(
        """
        Senior Java Backend Engineer
        Desert Systems
        Chandler, AZ
        Hybrid
        Salary: $140,000-$165,000
        Requirements:
        - Java
        - Spring Boot
        - REST APIs
        - AWS
        - Kubernetes
        - SQL
        """
    )
    candidate = load_candidate_profile(PROFILE)
    result = ScoutPipeline(
        llm_client=MockLLMClient(),
        thresholds=ScoutThresholds(),
        session=session,
    ).evaluate(extraction.normalized_job, candidate, persist=True, create_job_record=True)
    session.commit()
    assert result.evaluation.recommendation in {
        Recommendation.STRONG_RECOMMEND,
        Recommendation.RECOMMEND,
    }
    assert approval_service.can_enter_application_pipeline(result.job.id) is False


def test_duplicate_url_reuses_job_and_appends_evaluation(session) -> None:
    extraction = JobIngestionService().ingest_text(
        "Backend Engineer\nCo\nRemote\nSalary: $150000-$160000\nRequirements:\n- Java\n",
        source_url="https://example.com/jobs/dup-1",
    )
    # Force source_url onto normalized job
    extraction.normalized_job.source_url = "https://example.com/jobs/dup-1"
    candidate = load_candidate_profile(PROFILE)
    pipeline = ScoutPipeline(
        llm_client=MockLLMClient(),
        thresholds=ScoutThresholds(),
        session=session,
    )
    first = pipeline.evaluate(
        extraction.normalized_job, candidate, persist=True, create_job_record=True
    )
    second = pipeline.evaluate(
        extraction.normalized_job, candidate, persist=True, create_job_record=True
    )
    session.commit()
    assert first.job is not None and second.job is not None
    assert first.job.id == second.job.id
    from app.services.scout_evaluation_service import ScoutEvaluationService

    rows = ScoutEvaluationService(session).list_for_job(first.job.id)
    assert len(rows) >= 2


def test_scout_ingest_view_has_three_actions() -> None:
    from app.config import Settings

    view = ScoutIngestView(Settings())
    labels = [item.label for item in view.children if hasattr(item, "label")]
    assert "TEST FIXTURE" in labels
    assert "JOB URL" in labels
    assert "PASTE JOB" in labels


def test_partial_content_is_flagged() -> None:
    result = JobIngestionService().ingest_text(
        "Software Engineer\nAcme\n" + ("x" * 100),
        partial_content=True,
    )
    assert result.partial_content is True
    assert any("partial" in w.lower() for w in result.warnings)

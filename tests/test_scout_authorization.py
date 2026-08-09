"""Scout must not create approvals or enter the application pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agents.scout.llm.base import DeterministicContext
from app.agents.scout.llm.mock import MockLLMClient
from app.agents.scout.pipeline import ScoutEvaluationError, ScoutPipeline
from app.agents.scout.profile_loader import load_candidate_profile
from app.agents.scout.scoring import ScoutThresholds
from app.models.job import JobStatus
from app.schemas.evaluation import Recommendation, ScoutEvaluation
from app.schemas.job_posting import NormalizedJob
from app.services.approval_service import ApprovalService
from app.services.job_service import JobService
from app.services.scout_evaluation_service import ScoutEvaluationService


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "data" / "fixtures" / "profiles" / "test_remote_required.json"
JOB_A = ROOT / "data" / "fixtures" / "scout" / "fixture_a_strong_backend.json"


class BrokenLLM:
    provider_name = "broken"

    def evaluate_job(self, candidate, job, deterministic_context):
        # Invalid payload — pipeline must fail safely
        return ScoutEvaluation.model_validate(
            {
                "qualification_score": 999,  # will clamp, but recommendation invalid
                "desirability_score": 50,
                "recommendation": "NOT_A_REAL_RECOMMENDATION",
                "confidence": "HIGH",
            }
        )


class RaisingLLM:
    provider_name = "raising"

    def evaluate_job(self, candidate, job, deterministic_context):
        raise ValueError("provider exploded")


def test_malformed_evaluator_output_fails_safely() -> None:
    candidate = load_candidate_profile(PROFILE)
    job = NormalizedJob.model_validate(json.loads(JOB_A.read_text()))
    pipeline = ScoutPipeline(llm_client=BrokenLLM(), thresholds=ScoutThresholds())
    with pytest.raises(ScoutEvaluationError):
        pipeline.evaluate(job, candidate)


def test_llm_failure_does_not_fabricate_scores() -> None:
    candidate = load_candidate_profile(PROFILE)
    job = NormalizedJob.model_validate(json.loads(JOB_A.read_text()))
    pipeline = ScoutPipeline(llm_client=RaisingLLM(), thresholds=ScoutThresholds())
    with pytest.raises(ScoutEvaluationError):
        pipeline.evaluate(job, candidate)


def test_scout_cannot_create_approval(session, job_service: JobService, approval_service: ApprovalService) -> None:
    candidate = load_candidate_profile(PROFILE)
    job = NormalizedJob.model_validate(json.loads(JOB_A.read_text()))
    pipeline = ScoutPipeline(
        llm_client=MockLLMClient(),
        thresholds=ScoutThresholds(),
        session=session,
    )
    result = pipeline.evaluate(job, candidate, persist=True, create_job_record=True)
    session.commit()
    assert result.job is not None
    assert result.job.status_enum != JobStatus.APPROVED
    assert approval_service.get_approval_for_job(result.job.id) is None
    assert approval_service.can_enter_application_pipeline(result.job.id) is False


def test_recommended_job_cannot_enter_application_pipeline(
    session, approval_service: ApprovalService
) -> None:
    candidate = load_candidate_profile(PROFILE)
    job = NormalizedJob.model_validate(json.loads(JOB_A.read_text()))
    pipeline = ScoutPipeline(
        llm_client=MockLLMClient(),
        thresholds=ScoutThresholds(),
        session=session,
    )
    result = pipeline.evaluate(job, candidate, persist=True, create_job_record=True)
    session.commit()
    assert result.job is not None
    assert result.job.status_enum in {
        JobStatus.AWAITING_APPROVAL,
        JobStatus.ARCHIVED,
        JobStatus.SCORED,
        JobStatus.RECOMMENDED,
    }
    assert approval_service.can_enter_application_pipeline(result.job.id) is False


def test_exact_job_approval_invariant_still_works(
    session, approval_service: ApprovalService
) -> None:
    candidate = load_candidate_profile(PROFILE)
    job = NormalizedJob.model_validate(json.loads(JOB_A.read_text()))
    pipeline = ScoutPipeline(
        llm_client=MockLLMClient(),
        thresholds=ScoutThresholds(),
        session=session,
    )
    result = pipeline.evaluate(job, candidate, persist=True, create_job_record=True)
    session.commit()
    assert result.job is not None
    if result.job.status_enum != JobStatus.AWAITING_APPROVAL:
        # Force into awaiting for approval path test
        result.job.status = JobStatus.AWAITING_APPROVAL.value
        session.flush()
    approval_service.approve_job(result.job.id, approved_by="tester")
    assert approval_service.can_enter_application_pipeline(result.job.id) is True


def test_rejected_job_remains_unauthorized(
    session, approval_service: ApprovalService
) -> None:
    candidate = load_candidate_profile(PROFILE)
    job = NormalizedJob.model_validate(json.loads(JOB_A.read_text()))
    pipeline = ScoutPipeline(
        llm_client=MockLLMClient(),
        thresholds=ScoutThresholds(),
        session=session,
    )
    result = pipeline.evaluate(job, candidate, persist=True, create_job_record=True)
    session.commit()
    assert result.job is not None
    result.job.status = JobStatus.AWAITING_APPROVAL.value
    session.flush()
    approval_service.reject_job(result.job.id, rejected_by="tester")
    assert approval_service.can_enter_application_pipeline(result.job.id) is False
    with pytest.raises(Exception):
        approval_service.approve_job(result.job.id, approved_by="tester")


def test_evaluation_persistence_and_version_history(session) -> None:
    candidate = load_candidate_profile(PROFILE)
    job = NormalizedJob.model_validate(json.loads(JOB_A.read_text()))
    pipeline = ScoutPipeline(
        llm_client=MockLLMClient(evaluator_version="2a.1"),
        thresholds=ScoutThresholds(),
        session=session,
    )
    first = pipeline.evaluate(job, candidate, persist=True, create_job_record=True)
    session.flush()
    assert first.job is not None
    svc = ScoutEvaluationService(session)
    # Second evaluation with different version on same job
    second_eval = pipeline.evaluate(
        job,
        candidate,
        persist=True,
        create_job_record=False,
        job_id=first.job.id,
    )
    # Manually save with alternate version
    second_eval.evaluation.evaluator_version = "2a.2-test"
    svc.save_evaluation(first.job.id, second_eval.evaluation)
    session.commit()
    rows = svc.list_for_job(first.job.id)
    assert len(rows) >= 2
    versions = {r.evaluator_version for r in rows}
    assert "2a.1" in versions or any(v.startswith("2a") for v in versions)
    assert "2a.2-test" in versions

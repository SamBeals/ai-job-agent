"""Phase 2A.7 / Phase 3 foundation — multi-agent orchestration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.agents.applicant.agent import ApplicantAgent, UnauthorizedApplicationError
from app.agents.resume.agent import ResumeAgent, ResumeAgentError
from app.models.job import JobStatus
from app.models.resume_plan import ResumePlanRecord
from app.models.submission_authorization import SubmissionAuthorization
from app.schemas.agents import AgentType, PipelineStatus, WorkItemStatus, WorkItemTaskType
from app.schemas.evaluation import Confidence, Recommendation, ScoutEvaluation
from app.services.approval_service import ApprovalService
from app.services.job_service import JobService
from app.services.notifications import RecordingNotificationService
from app.services.pipeline_orchestrator import OrchestrationError, PipelineOrchestrator
from app.services.scout_evaluation_service import ScoutEvaluationService
from app.services.work_item_service import WorkItemService
from app.workers.agent_worker import process_one
from tests.conftest import make_job

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "data" / "fixtures" / "profiles" / "test_remote_required.json"
JOB_A = ROOT / "data" / "fixtures" / "scout" / "fixture_a_strong_backend.json"


def _seed_scout_evaluation(session: Session, job_id: int) -> ScoutEvaluation:
    evaluation = ScoutEvaluation(
        job_id=job_id,
        qualification_score=82,
        desirability_score=90,
        recommendation=Recommendation.RECOMMEND,
        confidence=Confidence.MEDIUM,
        matching_skills=["Java — professional experience", "REST APIs — professional experience"],
        partial_matches=["Terraform — listed skill"],
        missing_preferred_skills=["Kafka", "Go"],
        experience_matches=["Approximately 8 years professional software engineering"],
        qualification_reasoning=["Strong backend alignment."],
        desirability_reasoning=["Remote acceptable."],
        requirement_matches=[
            {
                "requirement": {
                    "id": "1",
                    "name": "Java",
                    "category": "SKILL",
                    "requirement_type": "REQUIRED",
                },
                "match_level": "STRONG_MATCH",
                "evidence_strength": "PROFESSIONAL_EXPERIENCE",
                "candidate_evidence": ["Java at Allstate"],
                "reasoning": "Professional Java experience",
            },
            {
                "requirement": {
                    "id": "2",
                    "name": "Kafka",
                    "category": "SKILL",
                    "requirement_type": "PREFERRED",
                },
                "match_level": "NO_EVIDENCE",
                "evidence_strength": "UNKNOWN",
                "candidate_evidence": [],
                "reasoning": "No verified Kafka evidence",
            },
            {
                "requirement": {
                    "id": "3",
                    "name": "Go",
                    "category": "SKILL",
                    "requirement_type": "PREFERRED",
                },
                "match_level": "NO_EVIDENCE",
                "evidence_strength": "UNKNOWN",
                "candidate_evidence": [],
                "reasoning": "No verified Go evidence",
            },
            {
                "requirement": {
                    "id": "4",
                    "name": "Terraform",
                    "category": "SKILL",
                    "requirement_type": "PREFERRED",
                },
                "match_level": "PARTIAL_MATCH",
                "evidence_strength": "LISTED_SKILL",
                "candidate_evidence": ["Listed skill"],
                "reasoning": "Listed skill; depth unknown",
            },
        ],
        evaluator_provider="mock",
        prompt_version="qualification-v1",
    )
    ScoutEvaluationService(session).save_evaluation(job_id, evaluation)
    session.flush()
    return evaluation


def test_scout_recommendation_does_not_create_pipeline(session, job_service: JobService) -> None:
    job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
    _seed_scout_evaluation(session, job.id)
    orch = PipelineOrchestrator(session)
    assert orch.get_pipeline_for_job(job.id) is None


def test_approval_creates_pipeline_and_resume_work(
    session, job_service: JobService, approval_service: ApprovalService
) -> None:
    job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
    _seed_scout_evaluation(session, job.id)
    approval_service.approve_job(job.id, approved_by="Sam (1)")
    recorder = RecordingNotificationService()
    orch = PipelineOrchestrator(session, notifications=recorder)
    result = orch.on_job_preparation_approved(job.id)
    assert result.created_pipeline is True
    assert result.created_work_item is True
    assert result.pipeline.status == PipelineStatus.PREPARATION_QUEUED.value
    item = WorkItemService(session).get(result.work_item_id)
    assert item is not None
    assert item.agent_type == AgentType.RESUME.value
    assert item.status == WorkItemStatus.PENDING.value
    assert any(e.kind.startswith("pipeline") for e in recorder.events)


def test_duplicate_orchestration_idempotent(
    session, job_service: JobService, approval_service: ApprovalService
) -> None:
    job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
    _seed_scout_evaluation(session, job.id)
    approval_service.approve_job(job.id, approved_by="Sam (1)")
    orch = PipelineOrchestrator(session)
    first = orch.on_job_preparation_approved(job.id)
    second = orch.on_job_preparation_approved(job.id)
    assert first.pipeline.id == second.pipeline.id
    assert first.work_item_id == second.work_item_id
    assert second.created_pipeline is False
    assert second.created_work_item is False
    items = WorkItemService(session).list_pending()
    assert len([i for i in items if i.job_id == job.id]) == 1


def test_approval_job_a_cannot_create_job_b_pipeline(
    session, job_service: JobService, approval_service: ApprovalService
) -> None:
    a = make_job(job_service, title="A", status=JobStatus.AWAITING_APPROVAL)
    b = make_job(job_service, title="B", status=JobStatus.AWAITING_APPROVAL)
    _seed_scout_evaluation(session, a.id)
    approval_service.approve_job(a.id, approved_by="Sam (1)")
    orch = PipelineOrchestrator(session)
    orch.on_job_preparation_approved(a.id)
    with pytest.raises(OrchestrationError):
        orch.on_job_preparation_approved(b.id)
    assert orch.get_pipeline_for_job(b.id) is None


def test_forged_approved_status_cannot_create_pipeline(
    session, job_service: JobService
) -> None:
    job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
    job.status = JobStatus.APPROVED.value
    orch = PipelineOrchestrator(session)
    with pytest.raises(OrchestrationError):
        orch.on_job_preparation_approved(job.id)


def test_resume_agent_refuses_unapproved_and_missing_pipeline(
    session, job_service: JobService
) -> None:
    job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
    agent = ResumeAgent(session, candidate_profile_path=str(PROFILE))
    result = agent.generate_for_job(job.id)
    assert result.success is False
    assert "not authorized" in result.message.lower()


def test_full_orchestration_to_resume_plan_ready(
    session, job_service: JobService, approval_service: ApprovalService
) -> None:
    job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL, company="GitHub")
    # Enrich description for NormalizedJob mapping
    job.description = json.loads(JOB_A.read_text())["description"]
    _seed_scout_evaluation(session, job.id)
    approval_service.approve_job(job.id, approved_by="Sam Beals (42)")
    recorder = RecordingNotificationService()
    orch = PipelineOrchestrator(session, notifications=recorder)
    orch_result = orch.on_job_preparation_approved(job.id)
    session.commit()

    # Worker claim + process
    work = WorkItemService(session)
    item = work.claim_next(worker_id="test-worker", agent_types=[AgentType.RESUME])
    assert item is not None
    assert item.id == orch_result.work_item_id
    orch.on_work_item_started(item.id)

    agent = ResumeAgent(
        session,
        candidate_profile_path=str(PROFILE),
        orchestrator=orch,
    )
    result = agent.process_work_item(item)
    session.commit()

    assert result.success is True
    assert result.resume_plan_id is not None
    pipeline = orch.get_pipeline_for_job(job.id)
    assert pipeline is not None
    assert pipeline.status == PipelineStatus.RESUME_PLAN_READY.value
    assert job.status_enum == JobStatus.RESUME_READY

    plan_row = session.get(ResumePlanRecord, result.resume_plan_id)
    assert plan_row is not None
    plan = plan_row.plan_json
    assert "Kafka" in plan["skills_not_to_claim"] or "Go" in plan["skills_not_to_claim"]
    claimed = [s["text"].lower() for s in plan["priority_skills"]]
    assert "kafka" not in claimed
    assert "go" not in claimed

    # Gate 2 locked
    assert approval_service.can_submit_application(pipeline.id) is False
    from sqlalchemy import select as sa_select

    assert (
        session.scalars(sa_select(SubmissionAuthorization)).first() is None
    )

    # Applicant not invoked / cannot submit
    with pytest.raises(UnauthorizedApplicationError):
        ApplicantAgent(session).apply_to_job(job.id)

    # Double-claim fails
    again = work.claim_next(worker_id="other", agent_types=[AgentType.RESUME])
    assert again is None or again.id != item.id


def test_work_item_cannot_be_claimed_twice(
    session, job_service: JobService, approval_service: ApprovalService
) -> None:
    job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
    _seed_scout_evaluation(session, job.id)
    approval_service.approve_job(job.id, approved_by="Sam (1)")
    PipelineOrchestrator(session).on_job_preparation_approved(job.id)
    work = WorkItemService(session)
    first = work.claim_next(worker_id="w1")
    second = work.claim_next(worker_id="w2")
    assert first is not None
    assert second is None


def test_notification_failure_does_not_roll_back_business_work(
    session, job_service: JobService, approval_service: ApprovalService
) -> None:
    class BoomNotify:
        def notify(self, event) -> None:
            raise RuntimeError("discord down")

    job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
    _seed_scout_evaluation(session, job.id)
    approval_service.approve_job(job.id, approved_by="Sam (1)")
    orch = PipelineOrchestrator(session, notifications=BoomNotify())  # type: ignore[arg-type]
    result = orch.on_job_preparation_approved(job.id)
    assert result.pipeline.id is not None
    assert result.work_item_id is not None
    assert orch.get_pipeline_for_job(job.id) is not None


def test_preparation_approval_does_not_satisfy_submission(
    session, job_service: JobService, approval_service: ApprovalService
) -> None:
    job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
    _seed_scout_evaluation(session, job.id)
    approval_service.approve_job(job.id, approved_by="Sam (1)")
    pipeline = PipelineOrchestrator(session).on_job_preparation_approved(job.id).pipeline
    assert approval_service.can_prepare_application(job.id) is True
    assert approval_service.can_submit_application(pipeline.id) is False
    assert approval_service.can_enter_application_pipeline(job.id) is True


def test_perfect_scout_scores_do_not_authorize_submission(
    session, job_service: JobService, approval_service: ApprovalService
) -> None:
    job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
    evaluation = ScoutEvaluation(
        job_id=job.id,
        qualification_score=100,
        desirability_score=100,
        recommendation=Recommendation.STRONG_RECOMMEND,
        confidence=Confidence.HIGH,
    )
    ScoutEvaluationService(session).save_evaluation(job.id, evaluation)
    approval_service.approve_job(job.id, approved_by="Sam (1)")
    pipeline = PipelineOrchestrator(session).on_job_preparation_approved(job.id).pipeline
    assert approval_service.can_submit_application(pipeline.id) is False


def test_resume_agent_cannot_create_submission_authorization(
    session, job_service: JobService, approval_service: ApprovalService
) -> None:
    job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
    _seed_scout_evaluation(session, job.id)
    approval_service.approve_job(job.id, approved_by="Sam (1)")
    orch = PipelineOrchestrator(session)
    orch.on_job_preparation_approved(job.id)
    item = WorkItemService(session).claim_next(worker_id="w")
    assert item is not None
    orch.on_work_item_started(item.id)
    ResumeAgent(
        session, candidate_profile_path=str(PROFILE), orchestrator=orch
    ).process_work_item(item)
    from sqlalchemy import select as sa_select

    assert session.scalars(sa_select(SubmissionAuthorization)).first() is None

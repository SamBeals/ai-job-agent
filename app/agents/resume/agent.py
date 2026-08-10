"""Resume Agent — builds ResumePlan for preparation-authorized jobs only."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agents.resume.plan_builder import AGENT_VERSION, build_resume_plan
from app.agents.resume.validator import (
    ResumePlanValidationError,
    assert_no_evidence_not_claimed,
    validate_resume_plan,
)
from app.agents.scout.profile_loader import CandidateProfileError, load_candidate_profile
from app.models.job import Job
from app.models.pipeline import ApplicationPipeline
from app.models.resume_plan import ResumePlanRecord
from app.models.work_item import AgentWorkItem
from app.schemas.agents import AgentType, WorkItemTaskType
from app.schemas.evaluation import ScoutEvaluation
from app.schemas.job_posting import NormalizedJob
from app.schemas.resume_plan import ResumePlan
from app.services.approval_service import ApprovalService
from app.services.pipeline_orchestrator import OrchestrationError, PipelineOrchestrator
from app.services.scout_evaluation_service import ScoutEvaluationService

logger = logging.getLogger(__name__)


class ResumeAgentError(Exception):
    """Resume Agent refused or failed safely."""


@dataclass
class ResumeResult:
    """Outcome of Resume Agent work."""

    job_id: int
    success: bool
    message: str
    resume_path: str | None = None
    resume_plan_id: int | None = None
    plan: ResumePlan | None = None


class ResumeAgent:
    """Creates a ResumePlan only for explicitly preparation-authorized jobs.

    May NOT invent candidate facts, approve jobs, submit applications,
    or create SubmissionAuthorization.
    """

    def __init__(
        self,
        session: Session,
        *,
        candidate_profile_path: str = "data/candidate_profile.json",
        orchestrator: PipelineOrchestrator | None = None,
    ) -> None:
        self.session = session
        self.candidate_profile_path = candidate_profile_path
        self.approval_service = ApprovalService(session)
        self.orchestrator = orchestrator or PipelineOrchestrator(session)
        self.eval_service = ScoutEvaluationService(session)

    def generate_for_job(self, job_id: int) -> ResumeResult:
        """Legacy entrypoint — refuses without authorization; prefers work-item path."""
        if not self.approval_service.can_prepare_application(job_id):
            return ResumeResult(
                job_id=job_id,
                success=False,
                message=(
                    f"Job {job_id} is not authorized for resume preparation. "
                    "Explicit preparation Approval is required."
                ),
            )
        pipeline = self.orchestrator.get_pipeline_for_job(job_id)
        if pipeline is None:
            return ResumeResult(
                job_id=job_id,
                success=False,
                message=f"Job {job_id} has no ApplicationPipeline — approve via Discord first.",
            )
        # Find resume work item
        item = self.orchestrator.work_items.find_for_pipeline_task(
            pipeline.id,
            AgentType.RESUME,
            WorkItemTaskType.BUILD_RESUME_PLAN,
        )
        if item is None:
            return ResumeResult(
                job_id=job_id,
                success=False,
                message="No Resume Agent work item found for pipeline.",
            )
        return self.process_work_item(item)

    def process_work_item(self, work_item: AgentWorkItem) -> ResumeResult:
        """Execute BUILD_RESUME_PLAN for a claimed work item."""
        job_id = work_item.job_id
        if work_item.agent_type != AgentType.RESUME.value:
            raise ResumeAgentError(f"Work item {work_item.id} is not a RESUME task")
        if work_item.task_type != WorkItemTaskType.BUILD_RESUME_PLAN.value:
            raise ResumeAgentError(
                f"Unsupported resume task type: {work_item.task_type}"
            )

        if not self.approval_service.can_prepare_application(job_id):
            raise ResumeAgentError(
                f"Job {job_id} lacks preparation authorization — Resume Agent refused"
            )

        pipeline = self.session.get(ApplicationPipeline, work_item.pipeline_id)
        if pipeline is None:
            raise ResumeAgentError(
                f"Missing ApplicationPipeline {work_item.pipeline_id} for job {job_id}"
            )
        if pipeline.job_id != job_id:
            raise ResumeAgentError("Pipeline/job mismatch — refusing work")

        # Gate 2 must remain locked — Resume Agent never creates submission auth
        if self.approval_service.can_submit_application(pipeline.id):
            logger.warning(
                "Unexpected submission authorization present for pipeline %s",
                pipeline.id,
            )

        job = self.session.get(Job, job_id)
        if job is None:
            raise ResumeAgentError(f"Job {job_id} not found")

        try:
            candidate = load_candidate_profile(self.candidate_profile_path)
        except CandidateProfileError as exc:
            raise ResumeAgentError(str(exc)) from exc

        normalized = _job_to_normalized(job)
        scout_row = self.eval_service.latest_for_job(job_id)
        if scout_row is None:
            raise ResumeAgentError(
                f"No ScoutEvaluation for job {job_id} — cannot build grounded ResumePlan"
            )
        evaluation = ScoutEvaluation.model_validate(scout_row.evaluation_json)

        plan = build_resume_plan(
            candidate=candidate,
            job=normalized,
            evaluation=evaluation,
            job_id=job_id,
            pipeline_id=pipeline.id,
            scout_evaluation_id=scout_row.id,
        )
        try:
            validate_resume_plan(plan, candidate)
            assert_no_evidence_not_claimed(plan)
        except ResumePlanValidationError as exc:
            raise ResumeAgentError(f"ResumePlan validation failed: {exc}") from exc

        record = ResumePlanRecord(
            job_id=job_id,
            pipeline_id=pipeline.id,
            plan_json=plan.model_dump(mode="json"),
            agent_version=AGENT_VERSION,
            validation_passed=True,
        )
        self.session.add(record)
        self.session.flush()

        self.orchestrator.on_resume_plan_completed(
            work_item_id=work_item.id,
            resume_plan_id=record.id,
        )

        logger.info(
            "resume_plan_created plan_id=%s job_id=%s pipeline_id=%s",
            record.id,
            job_id,
            pipeline.id,
        )
        return ResumeResult(
            job_id=job_id,
            success=True,
            message="ResumePlan prepared and persisted.",
            resume_plan_id=record.id,
            plan=plan,
        )


def _job_to_normalized(job: Job) -> NormalizedJob:
    return NormalizedJob(
        company=job.company,
        title=job.title,
        location=job.location,
        remote_status=job.remote_status,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        description=job.description,
        source_url=job.job_url,
        source=job.source or "manual",
        external_id=job.external_id,
    )

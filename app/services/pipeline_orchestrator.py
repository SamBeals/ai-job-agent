"""PipelineOrchestrator — structured handoffs between agents via persisted state."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.job import Job, JobStatus
from app.models.pipeline import ApplicationPipeline
from app.schemas.agents import (
    AgentType,
    PipelineStatus,
    WorkItemTaskType,
)
from app.services.approval_service import ApprovalService
from app.services.notifications import NotificationEvent, NotificationService, NullNotificationService
from app.services.work_item_service import WorkItemService

logger = logging.getLogger(__name__)


class OrchestrationError(Exception):
    """Raised when orchestration cannot proceed safely."""


@dataclass
class OrchestrationResult:
    pipeline: ApplicationPipeline
    work_item_id: int | None
    created_pipeline: bool
    created_work_item: bool


class PipelineOrchestrator:
    """React to domain transitions; create work items; advance pipeline status.

    Agents do not chat with each other — they consume structured persisted state.
    """

    def __init__(
        self,
        session: Session,
        *,
        notifications: NotificationService | None = None,
    ) -> None:
        self.session = session
        self.approvals = ApprovalService(session)
        self.work_items = WorkItemService(session)
        self.notifications = notifications or NullNotificationService()

    def _notify(self, event: NotificationEvent) -> None:
        """Best-effort notifications — never roll back business state."""
        try:
            self.notifications.notify(event)
        except Exception:  # noqa: BLE001
            logger.exception(
                "notification_failed kind=%s job_id=%s (non-fatal)",
                event.kind,
                event.job_id,
            )

    def on_job_preparation_approved(self, job_id: int) -> OrchestrationResult:
        """Gate 1 succeeded — create/reuse ApplicationPipeline + Resume work item.

        Idempotent: duplicate approvals / orchestration calls reuse existing rows.
        """
        if not self.approvals.can_prepare_application(job_id):
            raise OrchestrationError(
                f"Job {job_id} lacks preparation authorization; "
                "cannot create application pipeline"
            )

        job = self.session.get(Job, job_id)
        if job is None:
            raise OrchestrationError(f"Job {job_id} not found")

        pipeline, created_pipeline = self._get_or_create_pipeline(job)
        work_item, created_work = self.work_items.create_if_absent(
            job_id=job.id,
            pipeline_id=pipeline.id,
            agent_type=AgentType.RESUME,
            task_type=WorkItemTaskType.BUILD_RESUME_PLAN,
            input_metadata={"reason": "preparation_approved"},
        )
        if created_work and pipeline.status == PipelineStatus.PREPARATION_QUEUED.value:
            pipeline.current_agent = AgentType.RESUME.value
            pipeline.updated_at = datetime.now(timezone.utc)
            self.session.flush()

        self._notify(
            NotificationEvent(
                kind="pipeline_created" if created_pipeline else "pipeline_reused",
                title="APPLICATION PREPARATION APPROVED",
                body=(
                    f"{job.company} — {job.title}\n"
                    f"Pipeline #{pipeline.id} "
                    f"{'created' if created_pipeline else 'already exists'}.\n"
                    f"Next agent: RESUME AGENT\n"
                    f"Work item #{work_item.id}: "
                    f"{'QUEUED' if work_item.status == 'PENDING' else work_item.status}\n\n"
                    "This authorizes preparation only. "
                    "Final application submission will require separate approval."
                ),
                job_id=job.id,
                pipeline_id=pipeline.id,
                work_item_id=work_item.id,
                agent_type=AgentType.RESUME.value,
            )
        )
        return OrchestrationResult(
            pipeline=pipeline,
            work_item_id=work_item.id,
            created_pipeline=created_pipeline,
            created_work_item=created_work,
        )

    def on_work_item_started(self, work_item_id: int) -> ApplicationPipeline | None:
        item = self.work_items.get(work_item_id)
        if item is None:
            raise OrchestrationError(f"Work item {work_item_id} not found")

        from app.schemas.agents import WorkItemStatus

        if item.agent_type == AgentType.DISCOVERY.value:
            self._notify_discovery_started(item)
            return None

        pipeline = self.session.get(ApplicationPipeline, item.pipeline_id)
        if pipeline is None:
            raise OrchestrationError(f"Pipeline {item.pipeline_id} not found")

        job = self.session.get(Job, item.job_id)
        if item.agent_type == AgentType.RESUME.value:
            pipeline.status = PipelineStatus.RESUME_PLANNING.value
            pipeline.current_agent = AgentType.RESUME.value
            pipeline.updated_at = datetime.now(timezone.utc)
            if job and job.status_enum == JobStatus.APPROVED:
                job.transition_to(JobStatus.GENERATING_RESUME)
            self.session.flush()

        # Truthful: only notify after work item is RUNNING (caller claims first)
        from app.discord.agent_activity import resume_started_embeds

        if item.status != WorkItemStatus.RUNNING.value:
            logger.warning(
                "work_item_started called but status=%s id=%s — skipping activity notify",
                item.status,
                item.id,
            )
        else:
            company = job.company if job else "?"
            title = job.title if job else "?"
            embeds = resume_started_embeds(
                company=company,
                title=title,
                pipeline_id=pipeline.id,
                work_item_id=item.id,
            )
            self._notify(
                NotificationEvent(
                    kind="work_item_started",
                    title="RESUME AGENT",
                    body=(
                        f"Working on: {company} — {title}\n"
                        f"Task: Build tailored resume plan\n"
                        f"Status: RUNNING"
                    ),
                    job_id=item.job_id,
                    pipeline_id=pipeline.id,
                    work_item_id=item.id,
                    agent_type=item.agent_type,
                    semantic_type="RESUME_STARTED",
                    metadata={
                        "embeds": embeds,
                        "status": "RUNNING",
                        "semantic_type": "RESUME_STARTED",
                    },
                )
            )
        return pipeline

    def on_discovery_completed(self, work_item_id: int, run_id: int) -> None:
        """Notify Discovery completion. Does not create Approval/pipeline/resume work."""
        from app.discord.agent_activity import (
            discovery_completed_embeds,
            discovery_failed_embeds,
            discovery_partial_embeds,
        )
        from app.models.discovery import DiscoveryRun
        from app.schemas.discovery import DiscoveryRunStatus

        item = self.work_items.get(work_item_id)
        run = self.session.get(DiscoveryRun, run_id)
        if item is None or run is None:
            return

        # Snapshot primitives while Session-bound — never pass ORM into notifiers.
        status = str(run.status)
        sources = len(run.providers_used or [])
        raw_count = int(run.raw_result_count)
        filtered_count = int(run.filtered_result_count)
        quality_count = int(getattr(run, "quality_result_count", 0) or 0)
        previously_seen = int(getattr(run, "previously_seen_count", 0) or 0)
        surfaced_count = int(run.surfaced_result_count)
        rid = int(run.id)
        wid = int(item.id)

        if status == DiscoveryRunStatus.FAILED.value:
            embeds = discovery_failed_embeds(run_id=rid, work_item_id=wid)
            kind = "work_item_failed"
            title = "DISCOVERY — FAILED"
            semantic = "DISCOVERY_FAILED"
        elif status == DiscoveryRunStatus.PARTIAL.value:
            embeds = discovery_partial_embeds(
                run_id=rid,
                work_item_id=wid,
                surfaced_result_count=surfaced_count,
                quality_result_count=quality_count,
                previously_seen_count=previously_seen,
            )
            kind = "work_item_completed"
            title = "DISCOVERY — PARTIAL"
            semantic = "DISCOVERY_COMPLETED"
        else:
            embeds = discovery_completed_embeds(
                run_id=rid,
                work_item_id=wid,
                sources_searched=sources,
                raw_result_count=raw_count,
                filtered_result_count=filtered_count,
                surfaced_result_count=surfaced_count,
                quality_result_count=quality_count,
                previously_seen_count=previously_seen,
            )
            kind = "work_item_completed"
            title = "DISCOVERY — COMPLETE"
            semantic = "DISCOVERY_COMPLETED"

        self._notify(
            NotificationEvent(
                kind=kind,
                title=title,
                body=embeds[0].get("description", title) if embeds else title,
                work_item_id=wid,
                agent_type=AgentType.DISCOVERY.value,
                semantic_type=semantic,
                metadata={
                    "embeds": embeds,
                    "discovery_run_id": rid,
                    "status": status,
                    "semantic_type": semantic,
                },
            )
        )

    def _notify_discovery_started(self, item) -> None:
        from app.discord.agent_activity import discovery_started_embeds
        from app.schemas.agents import WorkItemStatus

        # Capture primitives immediately — do not retain ORM for notify payload.
        work_item_id = int(item.id)
        discovery_run_id = (
            int(item.discovery_run_id) if item.discovery_run_id is not None else None
        )
        status = str(item.status)

        if status != WorkItemStatus.RUNNING.value:
            logger.warning(
                "discovery_started called but status=%s id=%s — skipping activity notify",
                status,
                work_item_id,
            )
            return
        embeds = discovery_started_embeds(
            work_item_id=work_item_id, run_id=discovery_run_id
        )
        self._notify(
            NotificationEvent(
                kind="work_item_started",
                title="DISCOVERY",
                body=(
                    "Searching for current software engineering opportunities.\n"
                    "Status: RUNNING"
                ),
                work_item_id=work_item_id,
                agent_type=AgentType.DISCOVERY.value,
                semantic_type="DISCOVERY_STARTED",
                metadata={
                    "embeds": embeds,
                    "status": "RUNNING",
                    "discovery_run_id": discovery_run_id,
                    "semantic_type": "DISCOVERY_STARTED",
                },
            )
        )

    def on_resume_plan_completed(
        self,
        *,
        work_item_id: int,
        resume_plan_id: int,
    ) -> ApplicationPipeline:
        item = self.work_items.mark_completed(
            work_item_id,
            output_metadata={"resume_plan_id": resume_plan_id},
        )
        pipeline = self.session.get(ApplicationPipeline, item.pipeline_id)
        if pipeline is None:
            raise OrchestrationError(f"Pipeline {item.pipeline_id} not found")
        job = self.session.get(Job, item.job_id)

        pipeline.status = PipelineStatus.RESUME_PLAN_READY.value
        pipeline.current_agent = AgentType.RESUME.value
        pipeline.updated_at = datetime.now(timezone.utc)
        if job and job.status_enum == JobStatus.GENERATING_RESUME:
            job.transition_to(JobStatus.RESUME_READY)
        self.session.flush()

        from app.discord.agent_activity import resume_completed_embeds
        from app.models.resume_plan import ResumePlanRecord
        from app.schemas.resume_plan import ResumePlan

        plan: ResumePlan | None = None
        record = self.session.get(ResumePlanRecord, resume_plan_id)
        if record is not None:
            plan = ResumePlan.model_validate(record.plan_json)

        company = job.company if job else "?"
        title = job.title if job else "?"
        embeds = resume_completed_embeds(
            company=company,
            title=title,
            pipeline_status=pipeline.status,
            job_id=item.job_id,
            plan=plan,
        )
        # Intentionally do NOT create Applicant / submission work.
        self._notify(
            NotificationEvent(
                kind="work_item_completed",
                title="RESUME AGENT — COMPLETE",
                body=(
                    f"{company} — {title}\n"
                    f"Resume strategy prepared.\n"
                    f"Pipeline: {pipeline.status}"
                ),
                job_id=item.job_id,
                pipeline_id=pipeline.id,
                work_item_id=item.id,
                agent_type=item.agent_type,
                semantic_type="RESUME_COMPLETED",
                metadata={
                    "embeds": embeds,
                    "resume_plan_id": resume_plan_id,
                    "status": "COMPLETED",
                    "semantic_type": "RESUME_COMPLETED",
                    "emphasis": [
                        i.text for i in (plan.priority_skills if plan else [])[:6]
                    ],
                    "avoid": list(plan.skills_not_to_claim) if plan else [],
                },
            )
        )
        return pipeline

    def on_work_item_failed(
        self,
        work_item_id: int,
        *,
        error_message: str,
        permanent: bool = False,
        max_attempts: int = 3,
    ) -> ApplicationPipeline | None:
        from app.schemas.agents import WorkItemStatus as WIS

        item = self.work_items.mark_failed(
            work_item_id,
            error_message=error_message,
            permanent=permanent,
            max_attempts=max_attempts,
        )

        if item.agent_type == AgentType.DISCOVERY.value:
            if item.status == WIS.FAILED.value:
                from app.discord.agent_activity import discovery_failed_embeds
                from app.models.discovery import DiscoveryRun
                from app.schemas.discovery import DiscoveryRunStatus

                run = (
                    self.session.get(DiscoveryRun, item.discovery_run_id)
                    if item.discovery_run_id
                    else None
                )
                run_id = int(run.id) if run is not None else (
                    int(item.discovery_run_id) if item.discovery_run_id else None
                )
                wid = int(item.id)
                if run is not None and run.status in {
                    DiscoveryRunStatus.RUNNING.value,
                    DiscoveryRunStatus.QUEUED.value,
                }:
                    run.status = DiscoveryRunStatus.FAILED.value
                    run.error_summary = (error_message or "")[:500]
                    run.completed_at = datetime.now(timezone.utc)
                    self.session.flush()
                embeds = discovery_failed_embeds(run_id=run_id, work_item_id=wid)
                self._notify(
                    NotificationEvent(
                        kind="work_item_failed",
                        title="DISCOVERY — FAILED",
                        body="Discovery search failed. No fabricated results were posted.",
                        work_item_id=wid,
                        agent_type=AgentType.DISCOVERY.value,
                        semantic_type="DISCOVERY_FAILED",
                        metadata={
                            "embeds": embeds,
                            "status": "FAILED",
                            "semantic_type": "DISCOVERY_FAILED",
                        },
                    )
                )
            return None

        pipeline = self.session.get(ApplicationPipeline, item.pipeline_id)
        if pipeline is None:
            raise OrchestrationError(f"Pipeline {item.pipeline_id} not found")

        job = self.session.get(Job, item.job_id)
        if item.status == WIS.FAILED.value:
            pipeline.status = PipelineStatus.FAILED.value
            pipeline.error_message = error_message
            pipeline.updated_at = datetime.now(timezone.utc)
            if job and job.status_enum in {
                JobStatus.APPROVED,
                JobStatus.GENERATING_RESUME,
            }:
                try:
                    job.transition_to(JobStatus.FAILED)
                except Exception:  # noqa: BLE001
                    logger.warning("Could not transition job %s to FAILED", item.job_id)
            self.session.flush()

            from app.discord.agent_activity import resume_failed_embeds

            company = job.company if job else "?"
            title = job.title if job else "?"
            embeds = resume_failed_embeds(
                company=company,
                title=title,
                pipeline_status=pipeline.status,
                pipeline_id=pipeline.id,
            )
            # User-facing: no stack traces / secrets / raw provider errors
            self._notify(
                NotificationEvent(
                    kind="work_item_failed",
                    title="RESUME AGENT — FAILED",
                    body=(
                        f"{company} — {title}\n"
                        "I couldn't complete the resume plan.\n"
                        f"Pipeline status: {pipeline.status}"
                    ),
                    job_id=item.job_id,
                    pipeline_id=pipeline.id,
                    work_item_id=item.id,
                    agent_type=item.agent_type,
                    semantic_type="RESUME_FAILED",
                    metadata={
                        "embeds": embeds,
                        "status": "FAILED",
                        "semantic_type": "RESUME_FAILED",
                    },
                )
            )
        return pipeline

    def get_pipeline_for_job(self, job_id: int) -> ApplicationPipeline | None:
        stmt = select(ApplicationPipeline).where(ApplicationPipeline.job_id == job_id)
        return self.session.scalars(stmt).first()

    def list_active_pipelines(self, *, limit: int = 20) -> list[ApplicationPipeline]:
        active = {
            PipelineStatus.PREPARATION_QUEUED.value,
            PipelineStatus.RESUME_PLANNING.value,
            PipelineStatus.RESUME_PLAN_READY.value,
            PipelineStatus.BLOCKED.value,
        }
        stmt = (
            select(ApplicationPipeline)
            .where(ApplicationPipeline.status.in_(active))
            .order_by(ApplicationPipeline.updated_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt).all())

    def _get_or_create_pipeline(self, job: Job) -> tuple[ApplicationPipeline, bool]:
        existing = self.get_pipeline_for_job(job.id)
        if existing is not None:
            return existing, False
        approval = self.approvals.get_approval_for_job(job.id)
        pipeline = ApplicationPipeline(
            job_id=job.id,
            status=PipelineStatus.PREPARATION_QUEUED.value,
            current_agent=AgentType.RESUME.value,
            preparation_approved_at=(
                approval.approved_at if approval else datetime.now(timezone.utc)
            ),
        )
        try:
            with self.session.begin_nested():
                self.session.add(pipeline)
                self.session.flush()
        except IntegrityError:
            existing = self.get_pipeline_for_job(job.id)
            if existing is None:
                raise OrchestrationError(
                    f"Failed to create pipeline for job {job.id}"
                ) from None
            return existing, False
        return pipeline, True

"""Approval service — the ONLY path from AWAITING_APPROVAL to APPROVED."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.approval import Approval
from app.models.job import InvalidTransitionError, Job, JobStatus, validate_transition


class ApprovalError(Exception):
    """Base error for approval operations."""


class JobNotFoundError(ApprovalError):
    """Raised when the referenced job does not exist."""


class ApprovalNotAllowedError(ApprovalError):
    """Raised when approval cannot proceed (wrong status, rejected, etc.)."""


class DuplicateApprovalError(ApprovalError):
    """Raised when a job already has an approval record."""


@dataclass(frozen=True)
class ApprovalResult:
    """Outcome of an approval attempt."""

    approval: Approval
    job: Job
    already_approved: bool = False


class ApprovalService:
    """Deterministic authorization control plane.

    Rules:
    - Only this service may transition AWAITING_APPROVAL -> APPROVED.
    - Resume/Applicant agents must never call transition_to(APPROVED).
    - Approval is always tied to an exact job_id.
    - Conversational comments never create approvals.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def approve_job(
        self,
        job_id: int,
        *,
        approved_by: str,
        approval_source: str = "discord",
        approval_action: str = "approve",
        discord_message_id: str | None = None,
        discord_user_id: str | None = None,
    ) -> ApprovalResult:
        """Persist an approval record and transition the job to APPROVED.

        Safe against duplicate approvals: if already approved with a record,
        returns the existing approval without creating a second one.
        """
        job = self.session.get(Job, job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id} not found")

        existing = self.get_approval_for_job(job_id)
        if existing is not None:
            if job.status_enum == JobStatus.APPROVED:
                return ApprovalResult(approval=existing, job=job, already_approved=True)
            # Inconsistent state: approval exists but status is wrong — do not auto-fix.
            raise DuplicateApprovalError(
                f"Job {job_id} already has approval record {existing.id}"
            )

        if job.status_enum == JobStatus.REJECTED:
            raise ApprovalNotAllowedError(
                f"Job {job_id} is REJECTED and cannot be approved without "
                "an explicit recovery mechanism"
            )

        if job.status_enum != JobStatus.AWAITING_APPROVAL:
            raise ApprovalNotAllowedError(
                f"Job {job_id} status is {job.status}; "
                "only AWAITING_APPROVAL jobs can be approved"
            )

        # Structural check then apply — ApprovalService is the sole caller for APPROVED.
        validate_transition(job.status_enum, JobStatus.APPROVED)

        approval = Approval(
            job_id=job.id,
            approved_by=approved_by,
            approved_at=datetime.now(timezone.utc),
            approval_source=approval_source,
            approval_action=approval_action,
            discord_message_id=discord_message_id,
            discord_user_id=discord_user_id,
        )
        self.session.add(approval)

        job.status = JobStatus.APPROVED.value
        job.updated_at = datetime.now(timezone.utc)

        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateApprovalError(
                f"Job {job_id} already has an approval record"
            ) from exc

        return ApprovalResult(approval=approval, job=job, already_approved=False)

    def reject_job(self, job_id: int, *, rejected_by: str) -> Job:
        """Transition a job from AWAITING_APPROVAL to REJECTED."""
        job = self.session.get(Job, job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id} not found")

        if job.status_enum == JobStatus.REJECTED:
            return job

        if job.status_enum == JobStatus.APPROVED:
            raise ApprovalNotAllowedError(
                f"Job {job_id} is already APPROVED and cannot be rejected via this path"
            )

        try:
            job.transition_to(JobStatus.REJECTED)
        except InvalidTransitionError as exc:
            raise ApprovalNotAllowedError(str(exc)) from exc

        # rejected_by kept for future audit table; status change is the Phase 1 effect
        _ = rejected_by
        self.session.flush()
        return job

    def get_approval_for_job(self, job_id: int) -> Approval | None:
        """Return the approval record for a job, if any."""
        stmt = select(Approval).where(Approval.job_id == job_id)
        return self.session.scalars(stmt).first()

    def can_enter_application_pipeline(self, job_id: int) -> bool:
        """Return True only if the job is authorized for resume/application work.

        Alias conceptually for Gate 1: can_prepare_application.
        Requirements (both mandatory):
        1. job.status is APPROVED (or a later post-approval pipeline status)
        2. a valid Approval record exists for this exact job_id

        An approval for Job A never authorizes Job B.
        Does NOT authorize final submission (Gate 2).
        """
        return self.can_prepare_application(job_id)

    def can_prepare_application(self, job_id: int) -> bool:
        """Gate 1 — preparation authorization for this exact job."""
        job = self.session.get(Job, job_id)
        if job is None:
            return False

        if not self._is_post_approval_status(job.status_enum):
            return False

        approval = self.get_approval_for_job(job_id)
        if approval is None:
            return False

        return approval.job_id == job_id

    def can_submit_application(self, pipeline_id: int) -> bool:
        """Gate 2 — final submission authorization for this exact pipeline.

        Requires an explicit SubmissionAuthorization row.
        Preparation Approval alone NEVER satisfies this check.
        """
        from app.models.pipeline import ApplicationPipeline
        from app.models.submission_authorization import SubmissionAuthorization

        pipeline = self.session.get(ApplicationPipeline, pipeline_id)
        if pipeline is None:
            return False

        # Preparation authorization is necessary but never sufficient
        if not self.can_prepare_application(pipeline.job_id):
            return False

        stmt = select(SubmissionAuthorization).where(
            SubmissionAuthorization.pipeline_id == pipeline_id
        )
        auth = self.session.scalars(stmt).first()
        if auth is None:
            return False
        return auth.pipeline_id == pipeline_id and auth.job_id == pipeline.job_id

    @staticmethod
    def _is_post_approval_status(status: JobStatus) -> bool:
        """Statuses that imply the job has passed explicit preparation approval."""
        return status in {
            JobStatus.APPROVED,
            JobStatus.GENERATING_RESUME,
            JobStatus.RESUME_READY,
            JobStatus.READY_TO_APPLY,
            JobStatus.APPLYING,
            JobStatus.APPLIED,
            JobStatus.NEEDS_USER,
        }

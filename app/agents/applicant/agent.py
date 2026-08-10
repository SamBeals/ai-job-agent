"""Applicant Agent — application submission (placeholder).

Gate 1 (preparation Approval) is required but NOT sufficient for submission.
Gate 2 (SubmissionAuthorization) is required before any real submit — and is
never created in this phase.

Phase 3 foundation: no Playwright / browser automation or submission.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pipeline import ApplicationPipeline
from app.services.approval_service import ApprovalService


class UnauthorizedApplicationError(PermissionError):
    """Raised when application work is attempted without explicit approval."""


@dataclass
class ApplicationResult:
    """Placeholder result from an application attempt."""

    job_id: int
    success: bool = False
    message: str = "ApplicantAgent is a placeholder — no application submitted."


class ApplicantAgent:
    """May prepare/submit only with proper authorization gates. Currently a stub."""

    def __init__(
        self,
        session: Session,
        application_answers_path: str = "data/application_answers.example.json",
    ) -> None:
        self.session = session
        self.application_answers_path = application_answers_path
        self.approval_service = ApprovalService(session)

    def apply_to_job(self, job_id: int) -> ApplicationResult:
        """Refuse submission without Gate 2. Never submits in this phase."""
        if not self.approval_service.can_prepare_application(job_id):
            raise UnauthorizedApplicationError(
                f"Refusing to apply: job {job_id} lacks preparation Approval. "
                "NO APPLICATION WITHOUT EXPLICIT USER APPROVAL."
            )

        pipeline = self.session.scalars(
            select(ApplicationPipeline).where(ApplicationPipeline.job_id == job_id)
        ).first()
        if pipeline is None:
            raise UnauthorizedApplicationError(
                f"Refusing to apply: job {job_id} has no ApplicationPipeline."
            )

        if not self.approval_service.can_submit_application(pipeline.id):
            raise UnauthorizedApplicationError(
                f"Refusing to submit: pipeline {pipeline.id} lacks SubmissionAuthorization "
                "(Gate 2). Preparation Approval is not sufficient."
            )

        # Unreachable in normal Phase 3 foundation flows — no Gate 2 creator exists.
        return ApplicationResult(
            job_id=job_id,
            success=False,
            message=(
                "SubmissionAuthorization present, but ApplicantAgent is still a "
                "placeholder — no browser automation or submission performed."
            ),
        )

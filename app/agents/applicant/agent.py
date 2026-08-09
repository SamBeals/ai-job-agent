"""Applicant Agent — application submission (placeholder).

Eventually consumes:
  - An authorized job (APPROVED + Approval record)
  - Generated resume for that job
  - application_answers.json for recurring form fields
  - Employer application URL / ATS flow

Eventually produces:
  - Application attempt records
  - Status transitions: READY_TO_APPLY → APPLYING → APPLIED | NEEDS_USER | FAILED

AUTHORIZATION:
  This agent MUST call can_enter_application_pipeline(job_id) before any work.
  Without status-approved + matching Approval record, it must refuse.

Unknown form questions must eventually yield NEEDS_USER rather than guessing.

Phase 1: no Playwright / browser automation or submission.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.services.approval_service import ApprovalService


class UnauthorizedApplicationError(PermissionError):
    """Raised when application work is attempted without explicit approval."""


@dataclass
class ApplicationResult:
    """Placeholder result from an application attempt."""

    job_id: int
    success: bool = False
    message: str = "ApplicantAgent is a Phase 1 placeholder — no application submitted."


class ApplicantAgent:
    """Navigates employer sites and submits applications for authorized jobs only."""

    def __init__(
        self,
        session: Session,
        application_answers_path: str = "data/application_answers.example.json",
    ) -> None:
        self.session = session
        self.application_answers_path = application_answers_path
        self.approval_service = ApprovalService(session)

    def apply_to_job(self, job_id: int) -> ApplicationResult:
        """Placeholder apply. Explicitly checks authorization before anything else."""
        if not self.approval_service.can_enter_application_pipeline(job_id):
            raise UnauthorizedApplicationError(
                f"Refusing to apply: job {job_id} lacks explicit approval. "
                "NO APPLICATION WITHOUT EXPLICIT USER APPROVAL."
            )

        return ApplicationResult(
            job_id=job_id,
            success=False,
            message=(
                "Authorized, but ApplicantAgent is a Phase 1 placeholder — "
                "no browser automation or submission performed."
            ),
        )

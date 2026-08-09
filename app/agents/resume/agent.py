"""Resume Agent — tailored resume generation (placeholder).

Eventually consumes:
  - An APPROVED job (status + Approval record required upstream)
  - candidate_profile.json as the authoritative fact source
  - Job description / requirements

Eventually produces:
  - A tailored resume for that specific job
  - Job status transitions: APPROVED → GENERATING_RESUME → RESUME_READY

CRITICAL FUTURE RULE:
  The Resume Agent may select, reorder, summarize, and rephrase verified facts,
  but may NEVER invent skills, experience, employers, dates, certifications,
  education, metrics, or accomplishments.

Phase 1: no LLM calls or resume generation.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.services.approval_service import ApprovalService


@dataclass
class ResumeResult:
    """Placeholder result from resume generation."""

    job_id: int
    resume_path: str | None = None
    success: bool = False
    message: str = "ResumeAgent is a Phase 1 placeholder — no resume generated."


class ResumeAgent:
    """Creates a tailored resume only for explicitly approved jobs."""

    def __init__(
        self,
        session: Session,
        candidate_profile_path: str = "data/candidate_profile.example.json",
    ) -> None:
        self.session = session
        self.candidate_profile_path = candidate_profile_path
        self.approval_service = ApprovalService(session)

    def generate_for_job(self, job_id: int) -> ResumeResult:
        """Placeholder generation. Refuses to run without authorization."""
        if not self.approval_service.can_enter_application_pipeline(job_id):
            return ResumeResult(
                job_id=job_id,
                success=False,
                message=(
                    f"Job {job_id} is not authorized for resume generation. "
                    "Explicit user approval with a persisted Approval record is required."
                ),
            )

        return ResumeResult(
            job_id=job_id,
            success=False,
            message=(
                "Authorized, but ResumeAgent is a Phase 1 placeholder — "
                "no resume generated yet."
            ),
        )

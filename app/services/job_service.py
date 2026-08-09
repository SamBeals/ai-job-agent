"""Job service — CRUD and listing helpers for the control plane."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import Job, JobStatus, validate_transition


class JobNotFoundError(Exception):
    """Raised when a job cannot be found."""


class JobService:
    """Operations on Job records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_job(
        self,
        *,
        company: str,
        title: str,
        source: str = "manual",
        external_id: str | None = None,
        location: str | None = None,
        remote_status: str | None = None,
        salary_min: int | None = None,
        salary_max: int | None = None,
        job_url: str | None = None,
        description: str | None = None,
        fit_score: float | None = None,
        recommendation_reason: str | None = None,
        status: JobStatus = JobStatus.DISCOVERED,
    ) -> Job:
        """Create and persist a new job."""
        now = datetime.now(timezone.utc)
        job = Job(
            company=company,
            title=title,
            source=source,
            external_id=external_id,
            location=location,
            remote_status=remote_status,
            salary_min=salary_min,
            salary_max=salary_max,
            job_url=job_url,
            description=description,
            fit_score=fit_score,
            recommendation_reason=recommendation_reason,
            status=status.value,
            discovered_at=now,
            created_at=now,
            updated_at=now,
        )
        self.session.add(job)
        self.session.flush()
        return job

    def create_fake_recommendation(self) -> Job:
        """Insert a development-only fake job already awaiting approval."""
        reason = (
            "Strong matches:\n"
            "- Java\n"
            "- Spring Boot\n"
            "- AWS\n"
            "- Kubernetes\n\n"
            "Gap:\n"
            "- Terraform"
        )
        return self.create_job(
            company="Example Corp",
            title="Senior Software Engineer",
            source="test",
            external_id=f"fake-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            location="Remote",
            remote_status="Remote",
            salary_min=150000,
            salary_max=180000,
            job_url="https://example.com/jobs/senior-software-engineer",
            description=(
                "Fake development job for testing Discord approve/reject flow. "
                "Not a real opening."
            ),
            fit_score=0.91,
            recommendation_reason=reason,
            status=JobStatus.AWAITING_APPROVAL,
        )

    def get_job(self, job_id: int) -> Job | None:
        return self.session.get(Job, job_id)

    def require_job(self, job_id: int) -> Job:
        job = self.get_job(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id} not found")
        return job

    def list_awaiting_approval(self) -> list[Job]:
        stmt = (
            select(Job)
            .where(Job.status == JobStatus.AWAITING_APPROVAL.value)
            .order_by(Job.fit_score.desc().nullslast(), Job.discovered_at.desc())
        )
        return list(self.session.scalars(stmt).all())

    def list_by_status(self, status: JobStatus) -> list[Job]:
        stmt = select(Job).where(Job.status == status.value).order_by(Job.id.desc())
        return list(self.session.scalars(stmt).all())

    def transition(self, job_id: int, target: JobStatus) -> Job:
        """Apply a validated status transition.

        Refuses to transition directly to APPROVED — that path is owned by
        ApprovalService only.
        """
        if target == JobStatus.APPROVED:
            raise PermissionError(
                "Jobs may only be approved through ApprovalService.approve_job()"
            )

        job = self.require_job(job_id)
        validate_transition(job.status_enum, target)
        job.transition_to(target)
        self.session.flush()
        return job

    def count_jobs(self) -> int:
        from sqlalchemy import func

        return self.session.scalar(select(func.count()).select_from(Job)) or 0

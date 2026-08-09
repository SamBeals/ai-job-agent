"""Job model and lifecycle state machine."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base


class JobStatus(str, Enum):
    """Controlled job lifecycle states."""

    DISCOVERED = "DISCOVERED"
    SCORED = "SCORED"
    RECOMMENDED = "RECOMMENDED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    GENERATING_RESUME = "GENERATING_RESUME"
    RESUME_READY = "RESUME_READY"
    READY_TO_APPLY = "READY_TO_APPLY"
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"
    NEEDS_USER = "NEEDS_USER"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


# Allowed transitions. APPROVED may only be reached via ApprovalService.
ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.DISCOVERED: {JobStatus.SCORED, JobStatus.ARCHIVED, JobStatus.FAILED},
    JobStatus.SCORED: {
        JobStatus.RECOMMENDED,
        JobStatus.ARCHIVED,
        JobStatus.FAILED,
    },
    JobStatus.RECOMMENDED: {
        JobStatus.AWAITING_APPROVAL,
        JobStatus.ARCHIVED,
        JobStatus.FAILED,
    },
    JobStatus.AWAITING_APPROVAL: {
        JobStatus.APPROVED,  # ApprovalService only
        JobStatus.REJECTED,
        JobStatus.ARCHIVED,
        JobStatus.FAILED,
    },
    JobStatus.APPROVED: {
        JobStatus.GENERATING_RESUME,
        JobStatus.ARCHIVED,
        JobStatus.FAILED,
    },
    JobStatus.REJECTED: {
        JobStatus.ARCHIVED,
        # No path back to APPROVED without an explicit future recovery mechanism.
    },
    JobStatus.GENERATING_RESUME: {
        JobStatus.RESUME_READY,
        JobStatus.NEEDS_USER,
        JobStatus.FAILED,
    },
    JobStatus.RESUME_READY: {
        JobStatus.READY_TO_APPLY,
        JobStatus.NEEDS_USER,
        JobStatus.FAILED,
    },
    JobStatus.READY_TO_APPLY: {
        JobStatus.APPLYING,
        JobStatus.NEEDS_USER,
        JobStatus.FAILED,
        JobStatus.ARCHIVED,
    },
    JobStatus.APPLYING: {
        JobStatus.APPLIED,
        JobStatus.NEEDS_USER,
        JobStatus.FAILED,
    },
    JobStatus.APPLIED: {JobStatus.ARCHIVED},
    JobStatus.NEEDS_USER: {
        JobStatus.APPLYING,
        JobStatus.GENERATING_RESUME,
        JobStatus.READY_TO_APPLY,
        JobStatus.FAILED,
        JobStatus.ARCHIVED,
    },
    JobStatus.FAILED: {JobStatus.ARCHIVED, JobStatus.NEEDS_USER},
    JobStatus.ARCHIVED: set(),
}


class InvalidTransitionError(Exception):
    """Raised when a job status transition is not allowed."""

    def __init__(self, current: JobStatus, target: JobStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Invalid job status transition: {current.value} -> {target.value}"
        )


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    """Return whether a status transition is structurally allowed."""
    return target in ALLOWED_TRANSITIONS.get(current, set())


def validate_transition(current: JobStatus, target: JobStatus) -> None:
    """Raise InvalidTransitionError if the transition is not allowed."""
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    """A discovered or recommended job opportunity."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="manual")
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    remote_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    salary_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    job_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fit_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    recommendation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=JobStatus.DISCOVERED.value,
        index=True,
    )
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    approvals = relationship("Approval", back_populates="job", lazy="selectin")
    applications = relationship("Application", back_populates="job", lazy="selectin")

    @property
    def status_enum(self) -> JobStatus:
        return JobStatus(self.status)

    def transition_to(self, target: JobStatus) -> None:
        """Validate and apply a status transition.

        Transitioning to APPROVED is forbidden here. Use ApprovalService.approve_job(),
        which persists an Approval record and sets status atomically.
        """
        if target == JobStatus.APPROVED:
            raise PermissionError(
                "Cannot transition to APPROVED via Job.transition_to(). "
                "Use ApprovalService.approve_job() so an Approval record is persisted."
            )
        validate_transition(self.status_enum, target)
        self.status = target.value
        self.updated_at = utcnow()

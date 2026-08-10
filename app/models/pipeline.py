"""ApplicationPipeline — our attempt to apply to a Job opportunity.

Job = opportunity. ApplicationPipeline = preparation/submission workflow.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base
from app.schemas.agents import PipelineStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApplicationPipeline(Base):
    """Durable application-preparation workflow for exactly one job.

    Created only after Gate 1 preparation Approval.
    Does not imply Gate 2 submission authorization.
    """

    __tablename__ = "application_pipelines"
    __table_args__ = (UniqueConstraint("job_id", name="uq_pipeline_job_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=PipelineStatus.PREPARATION_QUEUED.value,
        index=True,
    )
    current_agent: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    preparation_approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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

    job = relationship("Job", back_populates="pipelines")
    work_items = relationship(
        "AgentWorkItem",
        back_populates="pipeline",
        lazy="selectin",
    )
    resume_plans = relationship(
        "ResumePlanRecord",
        back_populates="pipeline",
        lazy="selectin",
    )
    submission_authorizations = relationship(
        "SubmissionAuthorization",
        back_populates="pipeline",
        lazy="selectin",
    )

    @property
    def status_enum(self) -> PipelineStatus:
        return PipelineStatus(self.status)

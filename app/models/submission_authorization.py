"""Gate 2 — final submission authorization (foundation only; unused this phase)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SubmissionAuthorization(Base):
    """Explicit Gate 2 authorization to submit an application.

    Distinct from preparation Approval (Gate 1).
    No workflow in this phase creates these records — by design.
    """

    __tablename__ = "submission_authorizations"
    __table_args__ = (
        UniqueConstraint("pipeline_id", name="uq_submission_auth_pipeline"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("application_pipelines.id"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id"),
        nullable=False,
        index=True,
    )
    authorized_by: Mapped[str] = mapped_column(String(255), nullable=False)
    authorized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )
    authorization_source: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="discord",
    )
    discord_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    discord_message_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    pipeline = relationship("ApplicationPipeline", back_populates="submission_authorizations")
    job = relationship("Job")

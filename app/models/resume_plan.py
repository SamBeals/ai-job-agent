"""Persisted ResumePlan artifact."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResumePlanRecord(Base):
    """Stored ResumePlan JSON for a pipeline."""

    __tablename__ = "resume_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id"),
        nullable=False,
        index=True,
    )
    pipeline_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("application_pipelines.id"),
        nullable=False,
        index=True,
    )
    plan_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(SQLiteJSON(), "sqlite"),
        nullable=False,
    )
    agent_version: Mapped[str] = mapped_column(String(40), nullable=False, default="3.0.0")
    validation_passed: Mapped[bool] = mapped_column(nullable=False, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )

    pipeline = relationship("ApplicationPipeline", back_populates="resume_plans")
    job = relationship("Job")

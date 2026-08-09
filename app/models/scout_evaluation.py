"""SQLAlchemy persistence for Scout evaluations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ScoutEvaluationRecord(Base):
    """Persisted Scout evaluation. Multiple rows per job allow version history.

    Recommendation here is Scout judgment only — never authorization.
    """

    __tablename__ = "scout_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    qualification_score: Mapped[float] = mapped_column(Float, nullable=False)
    desirability_score: Mapped[float] = mapped_column(Float, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    evaluation_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    evaluator_provider: Mapped[str] = mapped_column(String(50), nullable=False, default="mock")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )

    job = relationship("Job", back_populates="scout_evaluations")

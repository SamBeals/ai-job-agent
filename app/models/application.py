"""Application attempt tracking (placeholder schema for future phases)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Application(Base):
    """Tracks an application attempt for an approved job.

    Phase 1: schema only. No real submission logic.
    """

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    resume_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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

    job = relationship("Job", back_populates="applications")

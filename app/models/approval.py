"""Persistent approval records — proof of explicit user authorization."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Approval(Base):
    """Record that a specific user explicitly approved a specific job.

    This is the authoritative authorization artifact. Conversational comments
    never create Approval rows. Only explicit Discord Approve actions (or
    future equivalent control-plane actions) do.
    """

    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_approvals_job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id"),
        nullable=False,
        index=True,
    )
    approved_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )
    approval_source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="discord",
    )
    approval_action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="approve",
    )
    # Optional Discord message metadata for audit trail
    discord_message_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    discord_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    job = relationship("Job", back_populates="approvals")

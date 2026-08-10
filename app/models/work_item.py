"""AgentWorkItem — durable structured handoff between agents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database.database import Base
from app.schemas.agents import AgentType, WorkItemStatus, WorkItemTaskType


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentWorkItem(Base):
    """Persisted unit of agent work. Agents communicate via these, not free-form chat.

    job_id / pipeline_id are required for post-approval agents (Resume).
    Discovery work may have null job/pipeline and set discovery_run_id instead.
    """

    __tablename__ = "agent_work_items"
    __table_args__ = (
        UniqueConstraint(
            "pipeline_id",
            "agent_type",
            "task_type",
            name="uq_work_item_pipeline_agent_task",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("jobs.id"),
        nullable=True,
        index=True,
    )
    pipeline_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("application_pipelines.id"),
        nullable=True,
        index=True,
    )
    discovery_run_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("discovery_runs.id"),
        nullable=True,
        index=True,
    )
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=WorkItemStatus.PENDING.value,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    input_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON().with_variant(SQLiteJSON(), "sqlite"), nullable=True
    )
    output_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON().with_variant(SQLiteJSON(), "sqlite"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    pipeline = relationship("ApplicationPipeline", back_populates="work_items")
    job = relationship("Job")

    @property
    def status_enum(self) -> WorkItemStatus:
        return WorkItemStatus(self.status)

    @property
    def agent_type_enum(self) -> AgentType:
        return AgentType(self.agent_type)

    @property
    def task_type_enum(self) -> WorkItemTaskType:
        return WorkItemTaskType(self.task_type)

"""AgentWorkItem claiming, completion, and retry helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.work_item import AgentWorkItem
from app.schemas.agents import AgentType, WorkItemStatus, WorkItemTaskType

logger = logging.getLogger(__name__)


class WorkItemError(Exception):
    """Base work-item error."""


class WorkItemClaimError(WorkItemError):
    """Raised when a work item cannot be claimed."""


class WorkItemService:
    """Durable work-item operations with atomic claim semantics."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, work_item_id: int) -> AgentWorkItem | None:
        return self.session.get(AgentWorkItem, work_item_id)

    def list_pending(self, *, limit: int = 20) -> list[AgentWorkItem]:
        stmt = (
            select(AgentWorkItem)
            .where(AgentWorkItem.status == WorkItemStatus.PENDING.value)
            .order_by(AgentWorkItem.created_at.asc(), AgentWorkItem.id.asc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt).all())

    def find_for_pipeline_task(
        self,
        pipeline_id: int,
        agent_type: AgentType,
        task_type: WorkItemTaskType,
    ) -> AgentWorkItem | None:
        stmt = select(AgentWorkItem).where(
            AgentWorkItem.pipeline_id == pipeline_id,
            AgentWorkItem.agent_type == agent_type.value,
            AgentWorkItem.task_type == task_type.value,
        )
        return self.session.scalars(stmt).first()

    def create_if_absent(
        self,
        *,
        job_id: int,
        pipeline_id: int,
        agent_type: AgentType,
        task_type: WorkItemTaskType,
        input_metadata: dict[str, Any] | None = None,
    ) -> tuple[AgentWorkItem, bool]:
        """Idempotent create. Returns (item, created)."""
        existing = self.find_for_pipeline_task(pipeline_id, agent_type, task_type)
        if existing is not None:
            return existing, False
        item = AgentWorkItem(
            job_id=job_id,
            pipeline_id=pipeline_id,
            agent_type=agent_type.value,
            task_type=task_type.value,
            status=WorkItemStatus.PENDING.value,
            input_metadata=input_metadata or {},
            attempt_count=0,
        )
        from sqlalchemy.exc import IntegrityError

        try:
            with self.session.begin_nested():
                self.session.add(item)
                self.session.flush()
        except IntegrityError:
            existing = self.find_for_pipeline_task(pipeline_id, agent_type, task_type)
            if existing is None:
                raise WorkItemError("Failed to create work item") from None
            return existing, False
        return item, True

    def claim_next(
        self,
        *,
        worker_id: str,
        agent_types: list[AgentType] | None = None,
    ) -> AgentWorkItem | None:
        """Atomically claim one PENDING work item.

        Uses UPDATE ... WHERE status=PENDING to avoid double-claim.
        SQLite note: under concurrent writers, prefer a single worker process;
        PostgreSQL row locking will improve this further.
        """
        stmt = (
            select(AgentWorkItem)
            .where(AgentWorkItem.status == WorkItemStatus.PENDING.value)
            .order_by(AgentWorkItem.created_at.asc(), AgentWorkItem.id.asc())
            .limit(1)
        )
        if agent_types:
            stmt = stmt.where(
                AgentWorkItem.agent_type.in_([a.value for a in agent_types])
            )
        candidate = self.session.scalars(stmt).first()
        if candidate is None:
            return None

        now = datetime.now(timezone.utc)
        result = self.session.execute(
            update(AgentWorkItem)
            .where(
                AgentWorkItem.id == candidate.id,
                AgentWorkItem.status == WorkItemStatus.PENDING.value,
            )
            .values(
                status=WorkItemStatus.RUNNING.value,
                started_at=now,
                heartbeat_at=now,
                claimed_by=worker_id,
                attempt_count=AgentWorkItem.attempt_count + 1,
            )
        )
        if result.rowcount != 1:
            self.session.rollback()
            return None
        self.session.flush()
        self.session.refresh(candidate)
        if candidate.status != WorkItemStatus.RUNNING.value:
            return None
        logger.info(
            "work_item_claimed id=%s agent=%s task=%s worker=%s attempt=%s",
            candidate.id,
            candidate.agent_type,
            candidate.task_type,
            worker_id,
            candidate.attempt_count,
        )
        return candidate

    def mark_completed(
        self,
        work_item_id: int,
        *,
        output_metadata: dict[str, Any] | None = None,
    ) -> AgentWorkItem:
        item = self._require(work_item_id)
        item.status = WorkItemStatus.COMPLETED.value
        item.completed_at = datetime.now(timezone.utc)
        item.heartbeat_at = item.completed_at
        item.error_message = None
        if output_metadata is not None:
            item.output_metadata = output_metadata
        self.session.flush()
        return item

    def mark_failed(
        self,
        work_item_id: int,
        *,
        error_message: str,
        permanent: bool = False,
        max_attempts: int = 3,
    ) -> AgentWorkItem:
        item = self._require(work_item_id)
        item.error_message = error_message
        item.failed_at = datetime.now(timezone.utc)
        item.heartbeat_at = item.failed_at
        if permanent or item.attempt_count >= max_attempts:
            item.status = WorkItemStatus.FAILED.value
        else:
            # Re-queue for another attempt
            item.status = WorkItemStatus.PENDING.value
            item.claimed_by = None
            item.started_at = None
        self.session.flush()
        return item

    def retry_failed(self, work_item_id: int) -> AgentWorkItem:
        """Manual retry for FAILED or stale RUNNING items."""
        item = self._require(work_item_id)
        if item.status not in {
            WorkItemStatus.FAILED.value,
            WorkItemStatus.RUNNING.value,
            WorkItemStatus.BLOCKED.value,
        }:
            raise WorkItemError(
                f"Work item {work_item_id} status {item.status} cannot be retried"
            )
        item.status = WorkItemStatus.PENDING.value
        item.claimed_by = None
        item.started_at = None
        item.error_message = None
        self.session.flush()
        return item

    def counts_by_agent(self, agent_type: AgentType) -> dict[str, int]:
        items = list(
            self.session.scalars(
                select(AgentWorkItem).where(AgentWorkItem.agent_type == agent_type.value)
            ).all()
        )
        counts = {s.value: 0 for s in WorkItemStatus}
        for item in items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    def _require(self, work_item_id: int) -> AgentWorkItem:
        item = self.get(work_item_id)
        if item is None:
            raise WorkItemError(f"Work item {work_item_id} not found")
        return item

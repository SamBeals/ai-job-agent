"""Background worker — claims AgentWorkItems and dispatches to agents.

Run:
  python -m app.workers.agent_worker

SQLite note: prefer a single worker process. Claim uses conditional UPDATE
compatible with PostgreSQL row semantics later.
"""

from __future__ import annotations

import argparse
import logging
import socket
import time
import uuid

from app.config import get_settings
from app.database.database import SessionLocal, init_db
from app.schemas.agents import AgentType, WorkItemTaskType
from app.services.notifications import build_notification_service
from app.services.pipeline_orchestrator import PipelineOrchestrator
from app.services.work_item_service import WorkItemService

logger = logging.getLogger(__name__)


class PermanentWorkError(Exception):
    """Non-retryable failure (auth, validation, missing data)."""


def process_one(*, worker_id: str, max_attempts: int) -> bool:
    """Claim and process one work item. Returns True if work was processed."""
    settings = get_settings()
    notifications = build_notification_service(settings)

    with SessionLocal() as session:
        work_items = WorkItemService(session)
        item = work_items.claim_next(
            worker_id=worker_id,
            agent_types=[AgentType.RESUME],
        )
        if item is None:
            return False

        orchestrator = PipelineOrchestrator(session, notifications=notifications)
        try:
            orchestrator.on_work_item_started(item.id)
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.exception("Failed to mark work item %s started", item.id)
            return True

    # Fresh session for agent work so notification failures after commit are safe
    with SessionLocal() as session:
        notifications = build_notification_service(settings)
        orchestrator = PipelineOrchestrator(session, notifications=notifications)
        work_items = WorkItemService(session)
        item = work_items.get(item.id)
        if item is None:
            return True

        try:
            if (
                item.agent_type == AgentType.RESUME.value
                and item.task_type == WorkItemTaskType.BUILD_RESUME_PLAN.value
            ):
                from app.agents.resume.agent import ResumeAgent, ResumeAgentError

                agent = ResumeAgent(
                    session,
                    candidate_profile_path=settings.candidate_profile_path,
                    orchestrator=orchestrator,
                )
                try:
                    result = agent.process_work_item(item)
                except ResumeAgentError as exc:
                    raise PermanentWorkError(str(exc)) from exc
                if not result.success:
                    raise PermanentWorkError(result.message)
                session.commit()
                logger.info(
                    "work_item_completed id=%s resume_plan_id=%s",
                    item.id,
                    result.resume_plan_id,
                )
            else:
                raise PermanentWorkError(
                    f"No handler for {item.agent_type}/{item.task_type}"
                )
        except PermanentWorkError as exc:
            session.rollback()
            with SessionLocal() as fail_session:
                fail_orch = PipelineOrchestrator(
                    fail_session,
                    notifications=build_notification_service(settings),
                )
                fail_orch.on_work_item_failed(
                    item.id,
                    error_message=str(exc),
                    permanent=True,
                    max_attempts=max_attempts,
                )
                fail_session.commit()
            logger.error("work_item_permanent_failure id=%s error=%s", item.id, exc)
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            with SessionLocal() as fail_session:
                fail_orch = PipelineOrchestrator(
                    fail_session,
                    notifications=build_notification_service(settings),
                )
                fail_orch.on_work_item_failed(
                    item.id,
                    error_message=str(exc),
                    permanent=False,
                    max_attempts=max_attempts,
                )
                fail_session.commit()
            logger.exception("work_item_retryable_failure id=%s", item.id)

    return True


def run_worker(*, poll_seconds: float, max_attempts: int) -> None:
    init_db()
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    logger.info(
        "agent_worker_started worker_id=%s poll=%ss max_attempts=%s",
        worker_id,
        poll_seconds,
        max_attempts,
    )
    while True:
        did_work = process_one(worker_id=worker_id, max_attempts=max_attempts)
        if not did_work:
            time.sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process AgentWorkItems")
    parser.add_argument("--poll", type=float, default=None, help="Poll interval seconds")
    parser.add_argument("--once", action="store_true", help="Process at most one item then exit")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    settings = get_settings()
    poll = args.poll if args.poll is not None else settings.agent_worker_poll_seconds
    max_attempts = settings.agent_max_attempts
    if args.once:
        init_db()
        worker_id = f"once-{uuid.uuid4().hex[:8]}"
        process_one(worker_id=worker_id, max_attempts=max_attempts)
        return 0
    run_worker(poll_seconds=poll, max_attempts=max_attempts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

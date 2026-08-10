"""Background worker — claims AgentWorkItems and dispatches to agents.

Run:
  python -m app.workers.agent_worker

Session rule: capture ClaimedWork primitives before closing the claim Session.
Never access ORM attributes after commit/close. Provider network I/O for
Discovery runs outside any open DB transaction.
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
from app.services.claimed_work import ClaimedWork
from app.services.notifications import build_notification_service
from app.services.pipeline_orchestrator import PipelineOrchestrator
from app.services.work_item_service import WorkItemService

logger = logging.getLogger(__name__)


class PermanentWorkError(Exception):
    """Non-retryable failure (auth, validation, missing data)."""


def _capture_claimed_work(item) -> ClaimedWork:
    """Read identity fields while the ORM instance is still Session-bound."""
    return ClaimedWork(
        work_item_id=int(item.id),
        agent_type=str(item.agent_type),
        task_type=str(item.task_type),
        job_id=int(item.job_id) if item.job_id is not None else None,
        pipeline_id=int(item.pipeline_id) if item.pipeline_id is not None else None,
        discovery_run_id=(
            int(item.discovery_run_id) if item.discovery_run_id is not None else None
        ),
    )


def process_one(*, worker_id: str, max_attempts: int) -> bool:
    """Claim and process one work item. Returns True if work was processed."""
    settings = get_settings()
    notifications = build_notification_service(settings)

    claimed: ClaimedWork | None = None
    with SessionLocal() as session:
        work_items = WorkItemService(session)
        item = work_items.claim_next(
            worker_id=worker_id,
            agent_types=[AgentType.RESUME, AgentType.DISCOVERY],
        )
        if item is None:
            return False

        # Capture primitives BEFORE commit/close — expire_on_commit + closed Session
        # makes later ORM attribute access raise DetachedInstanceError.
        claimed = _capture_claimed_work(item)
        orchestrator = PipelineOrchestrator(session, notifications=notifications)
        try:
            orchestrator.on_work_item_started(claimed.work_item_id)
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.exception(
                "Failed to mark work item %s started", claimed.work_item_id
            )
            return True

    assert claimed is not None

    try:
        if (
            claimed.agent_type == AgentType.RESUME.value
            and claimed.task_type == WorkItemTaskType.BUILD_RESUME_PLAN.value
        ):
            _process_resume(claimed, settings=settings, max_attempts=max_attempts)
        elif (
            claimed.agent_type == AgentType.DISCOVERY.value
            and claimed.task_type == WorkItemTaskType.SEARCH_JOBS.value
        ):
            _process_discovery(claimed, settings=settings, max_attempts=max_attempts)
        else:
            raise PermanentWorkError(
                f"No handler for {claimed.agent_type}/{claimed.task_type}"
            )
    except PermanentWorkError as exc:
        _fail_work(claimed, str(exc), permanent=True, max_attempts=max_attempts, settings=settings)
        logger.error(
            "work_item_permanent_failure id=%s error=%s",
            claimed.work_item_id,
            exc,
        )
    except Exception as exc:  # noqa: BLE001
        _fail_work(
            claimed, str(exc), permanent=False, max_attempts=max_attempts, settings=settings
        )
        logger.exception("work_item_retryable_failure id=%s", claimed.work_item_id)

    return True


def _process_resume(
    claimed: ClaimedWork,
    *,
    settings,
    max_attempts: int,
) -> None:
    from app.agents.resume.agent import ResumeAgent, ResumeAgentError

    with SessionLocal() as session:
        notifications = build_notification_service(settings)
        orchestrator = PipelineOrchestrator(session, notifications=notifications)
        agent = ResumeAgent(
            session,
            candidate_profile_path=settings.candidate_profile_path,
            orchestrator=orchestrator,
        )
        work_item = WorkItemService(session).get(claimed.work_item_id)
        if work_item is None:
            raise PermanentWorkError(f"Work item {claimed.work_item_id} missing")
        try:
            result = agent.process_work_item(work_item)
        except ResumeAgentError as exc:
            raise PermanentWorkError(str(exc)) from exc
        if not result.success:
            raise PermanentWorkError(result.message)
        session.commit()
        logger.info(
            "work_item_completed id=%s resume_plan_id=%s",
            claimed.work_item_id,
            result.resume_plan_id,
        )


def _process_discovery(
    claimed: ClaimedWork,
    *,
    settings,
    max_attempts: int,
) -> None:
    """Discovery: short DB txs around long provider I/O (no Session during network)."""
    from app.agents.discovery.agent import (
        DiscoveryAgent,
        DiscoveryAgentError,
        search_providers,
    )
    from app.agents.discovery.factory import build_discovery_providers
    from app.agents.discovery.queries import plan_discovery_query
    from app.agents.scout.profile_loader import load_candidate_profile

    if claimed.discovery_run_id is None:
        raise PermanentWorkError(
            f"Discovery work item {claimed.work_item_id} missing discovery_run_id"
        )

    run_id = claimed.discovery_run_id
    work_item_id = claimed.work_item_id

    profile = load_candidate_profile(settings.candidate_profile_path)
    query = plan_discovery_query(
        profile,
        max_raw_results=settings.discovery_max_raw_results,
    )
    providers = build_discovery_providers(settings)
    provider_names = [getattr(p, "name", type(p).__name__) for p in providers]

    # Short transaction: mark run RUNNING before network
    with SessionLocal() as session:
        agent = DiscoveryAgent(session, settings=settings)
        try:
            agent.mark_run_started(run_id, query=query, provider_names=provider_names)
            session.commit()
        except DiscoveryAgentError as exc:
            raise PermanentWorkError(str(exc)) from exc

    # Network / provider I/O — no open Session
    provider_outcome = search_providers(providers, query, run_id=run_id)

    # Short transaction: persist results + complete work item
    with SessionLocal() as session:
        notifications = build_notification_service(settings)
        agent = DiscoveryAgent(
            session,
            settings=settings,
            notifications=notifications,
        )
        try:
            result = agent.finalize_provider_outcome(
                run_id,
                work_item_id=work_item_id,
                provider_outcome=provider_outcome,
                profile=profile,
            )
        except DiscoveryAgentError as exc:
            raise PermanentWorkError(str(exc)) from exc
        session.commit()
        result_run_id = result.run_id
        result_status = result.status

    # Notify after commit so webhook failures cannot roll back business state
    with SessionLocal() as notify_session:
        notify_orch = PipelineOrchestrator(
            notify_session,
            notifications=build_notification_service(settings),
        )
        notify_orch.on_discovery_completed(work_item_id, result_run_id)
        notify_session.commit()

    logger.info(
        "work_item_completed id=%s discovery_run_id=%s status=%s",
        work_item_id,
        result_run_id,
        result_status,
    )


def _fail_work(
    claimed: ClaimedWork,
    error_message: str,
    *,
    permanent: bool,
    max_attempts: int,
    settings,
) -> None:
    with SessionLocal() as fail_session:
        fail_orch = PipelineOrchestrator(
            fail_session,
            notifications=build_notification_service(settings),
        )
        fail_orch.on_work_item_failed(
            claimed.work_item_id,
            error_message=error_message,
            permanent=permanent,
            max_attempts=max_attempts,
        )
        fail_session.commit()


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
    from app.logging_config import configure_logging, register_secret_value

    configure_logging()
    settings = get_settings()
    register_secret_value(settings.discord_agent_webhook_url)
    register_secret_value(settings.discord_bot_token)
    register_secret_value(settings.openai_api_key)
    register_secret_value(settings.adzuna_app_key)
    register_secret_value(settings.discovery_adzuna_app_key)
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

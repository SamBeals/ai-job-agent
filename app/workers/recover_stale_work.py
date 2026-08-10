"""Recover AgentWorkItems left RUNNING after a worker crash.

Usage:
  python -m app.workers.recover_stale_work --older-than-minutes 5
  python -m app.workers.recover_stale_work --older-than-minutes 0 --dry-run
  python -m app.workers.recover_stale_work --work-item-id 1

Does NOT reset work that still has a fresh heartbeat (genuinely active).
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.database import SessionLocal, init_db
from app.models.discovery import DiscoveryRun
from app.models.work_item import AgentWorkItem
from app.schemas.agents import AgentType, WorkItemStatus
from app.schemas.discovery import DiscoveryRunStatus

logger = logging.getLogger(__name__)


@dataclass
class RecoveryAction:
    work_item_id: int
    agent_type: str
    previous_status: str
    new_status: str
    discovery_run_id: int | None = None
    detail: str = ""


def list_stale_running(
    session: Session,
    *,
    older_than: timedelta,
    work_item_id: int | None = None,
    now: datetime | None = None,
) -> list[AgentWorkItem]:
    """RUNNING items whose started_at/heartbeat_at is older than the threshold."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - older_than
    stmt = select(AgentWorkItem).where(
        AgentWorkItem.status == WorkItemStatus.RUNNING.value
    )
    if work_item_id is not None:
        stmt = stmt.where(AgentWorkItem.id == work_item_id)
    items = list(session.scalars(stmt.order_by(AgentWorkItem.id.asc())).all())
    stale: list[AgentWorkItem] = []
    for item in items:
        # Prefer heartbeat; fall back to started_at
        stamp = item.heartbeat_at or item.started_at
        if stamp is None:
            stale.append(item)
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp <= cutoff:
            stale.append(item)
    return stale


def recover_stale_running(
    session: Session,
    *,
    older_than: timedelta,
    work_item_id: int | None = None,
    dry_run: bool = False,
    abandon_orphaned_discovery_runs: bool = True,
) -> list[RecoveryAction]:
    """Reset stale RUNNING work to PENDING so a worker can reclaim it.

    Also fails abandoned DiscoveryRuns that are QUEUED/RUNNING with no active work.
    """
    actions: list[RecoveryAction] = []
    stale = list_stale_running(
        session, older_than=older_than, work_item_id=work_item_id
    )
    for item in stale:
        run_id = item.discovery_run_id
        action = RecoveryAction(
            work_item_id=item.id,
            agent_type=item.agent_type,
            previous_status=item.status,
            new_status=WorkItemStatus.PENDING.value,
            discovery_run_id=run_id,
            detail="reset stale RUNNING → PENDING for reclaim",
        )
        if not dry_run:
            item.status = WorkItemStatus.PENDING.value
            item.claimed_by = None
            item.started_at = None
            item.heartbeat_at = None
            item.error_message = (
                "Recovered from stale RUNNING after worker crash/interrupt"
            )
            if run_id and item.agent_type == AgentType.DISCOVERY.value:
                run = session.get(DiscoveryRun, run_id)
                if run is not None and run.status in {
                    DiscoveryRunStatus.RUNNING.value,
                    DiscoveryRunStatus.QUEUED.value,
                }:
                    # Keep run recoverable; back to QUEUED until worker starts again
                    run.status = DiscoveryRunStatus.QUEUED.value
                    run.error_summary = "Work item recovered from stale RUNNING"
            session.flush()
        actions.append(action)
        logger.info(
            "stale_work_recovered work_item_id=%s agent=%s dry_run=%s",
            item.id,
            item.agent_type,
            dry_run,
        )

    if abandon_orphaned_discovery_runs and work_item_id is None:
        actions.extend(
            _abandon_orphaned_discovery_runs(session, dry_run=dry_run)
        )
        actions.extend(
            _collapse_duplicate_pending_discovery(session, dry_run=dry_run)
        )

    return actions


def _collapse_duplicate_pending_discovery(
    session: Session, *, dry_run: bool
) -> list[RecoveryAction]:
    """Keep the oldest PENDING Discovery work item; fail extras from repeated /discover."""
    actions: list[RecoveryAction] = []
    stmt = (
        select(AgentWorkItem)
        .where(
            AgentWorkItem.agent_type == AgentType.DISCOVERY.value,
            AgentWorkItem.task_type == "SEARCH_JOBS",
            AgentWorkItem.status == WorkItemStatus.PENDING.value,
        )
        .order_by(AgentWorkItem.id.asc())
    )
    pending = list(session.scalars(stmt).all())
    if len(pending) <= 1:
        return actions
    for item in pending[1:]:
        action = RecoveryAction(
            work_item_id=item.id,
            agent_type=item.agent_type,
            previous_status=item.status,
            new_status=WorkItemStatus.FAILED.value,
            discovery_run_id=item.discovery_run_id,
            detail="duplicate PENDING Discovery collapsed; kept oldest",
        )
        if not dry_run:
            item.status = WorkItemStatus.FAILED.value
            item.error_message = "Collapsed duplicate Discovery queue"
            if item.discovery_run_id:
                run = session.get(DiscoveryRun, item.discovery_run_id)
                if run is not None and run.status in {
                    DiscoveryRunStatus.QUEUED.value,
                    DiscoveryRunStatus.RUNNING.value,
                }:
                    run.status = DiscoveryRunStatus.FAILED.value
                    run.completed_at = datetime.now(timezone.utc)
                    run.error_summary = "Abandoned duplicate Discovery queue"
            session.flush()
        actions.append(action)
    return actions


def _abandon_orphaned_discovery_runs(
    session: Session, *, dry_run: bool
) -> list[RecoveryAction]:
    """Fail DiscoveryRuns stuck QUEUED/RUNNING whose work item is not active."""
    actions: list[RecoveryAction] = []
    runs = list(
        session.scalars(
            select(DiscoveryRun).where(
                DiscoveryRun.status.in_(
                    [
                        DiscoveryRunStatus.QUEUED.value,
                        DiscoveryRunStatus.RUNNING.value,
                    ]
                )
            )
        ).all()
    )
    for run in runs:
        item = None
        if run.work_item_id:
            item = session.get(AgentWorkItem, run.work_item_id)
        active = item is not None and item.status in {
            WorkItemStatus.PENDING.value,
            WorkItemStatus.RUNNING.value,
        }
        if active:
            continue
        action = RecoveryAction(
            work_item_id=run.work_item_id or 0,
            agent_type=AgentType.DISCOVERY.value,
            previous_status=run.status,
            new_status=DiscoveryRunStatus.FAILED.value,
            discovery_run_id=run.id,
            detail="abandoned DiscoveryRun with no active work item",
        )
        if not dry_run:
            run.status = DiscoveryRunStatus.FAILED.value
            run.completed_at = datetime.now(timezone.utc)
            run.error_summary = (
                run.error_summary
                or "Abandoned: no PENDING/RUNNING work item (duplicate or crash)"
            )
            if item is not None and item.status not in {
                WorkItemStatus.COMPLETED.value,
                WorkItemStatus.FAILED.value,
                WorkItemStatus.CANCELLED.value,
            }:
                item.status = WorkItemStatus.FAILED.value
                item.error_message = "Abandoned with DiscoveryRun cleanup"
            session.flush()
        actions.append(action)
    return actions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recover AgentWorkItems left RUNNING after a worker crash"
    )
    parser.add_argument(
        "--older-than-minutes",
        type=float,
        default=5.0,
        help="Only recover RUNNING items with started/heartbeat older than this (default 5)",
    )
    parser.add_argument(
        "--work-item-id",
        type=int,
        default=None,
        help="Recover a specific work item id (still respects freshness unless minutes=0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List actions without writing",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    init_db()
    older = timedelta(minutes=args.older_than_minutes)
    with SessionLocal() as session:
        actions = recover_stale_running(
            session,
            older_than=older,
            work_item_id=args.work_item_id,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            session.commit()
    if not actions:
        print("No stale RUNNING work found.")
        return 0
    for a in actions:
        print(
            f"{'[dry-run] ' if args.dry_run else ''}"
            f"work_item=#{a.work_item_id} agent={a.agent_type} "
            f"{a.previous_status}→{a.new_status} run=#{a.discovery_run_id} "
            f"({a.detail})"
        )
    print(f"{len(actions)} action(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

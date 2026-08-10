"""Discovery Agent — search, filter, dedupe, rank, persist. Never authorizes."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agents.discovery.dedupe import (
    dedupe_within_run,
    find_prior_identity,
    should_block_resurface,
)
from app.agents.discovery.factory import build_discovery_providers
from app.agents.discovery.filters import prefilter_candidate
from app.agents.discovery.queries import plan_discovery_query, query_debug_lines
from app.agents.discovery.ranking import score_candidate
from app.agents.scout.profile_loader import load_candidate_profile
from app.config import Settings, get_settings
from app.models.discovery import DiscoveryResult, DiscoveryRun
from app.models.work_item import AgentWorkItem
from app.schemas.agents import AgentType, WorkItemStatus, WorkItemTaskType
from app.schemas.discovery import (
    DiscoveryResultStatus,
    DiscoveryRunStatus,
    RankedDiscoveryCandidate,
    RawDiscoveryResult,
)
from app.services.notifications import NotificationEvent, NotificationService, NullNotificationService
from app.services.work_item_service import WorkItemService

logger = logging.getLogger(__name__)


class DiscoveryAgentError(Exception):
    """Permanent Discovery failure."""


@dataclass
class DiscoveryExecutionResult:
    run: DiscoveryRun
    surfaced: list[DiscoveryResult] = field(default_factory=list)
    success: bool = True
    message: str = ""


class DiscoveryAgent:
    """Find plausible opportunities. Does not approve, prepare, or submit."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        notifications: NotificationService | None = None,
        providers: list[Any] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.notifications = notifications or NullNotificationService()
        self.providers = providers
        self.work_items = WorkItemService(session)

    def process_work_item(self, work_item: AgentWorkItem) -> DiscoveryExecutionResult:
        if work_item.agent_type != AgentType.DISCOVERY.value:
            raise DiscoveryAgentError(
                f"DiscoveryAgent cannot process agent_type={work_item.agent_type}"
            )
        if work_item.task_type != WorkItemTaskType.SEARCH_JOBS.value:
            raise DiscoveryAgentError(
                f"Unsupported Discovery task: {work_item.task_type}"
            )
        if work_item.status != WorkItemStatus.RUNNING.value:
            raise DiscoveryAgentError(
                f"Work item {work_item.id} must be RUNNING (got {work_item.status})"
            )

        run = None
        if work_item.discovery_run_id:
            run = self.session.get(DiscoveryRun, work_item.discovery_run_id)
        if run is None:
            raise DiscoveryAgentError("Discovery work item missing DiscoveryRun")

        return self.execute_run(run, work_item=work_item)

    def execute_run(
        self,
        run: DiscoveryRun,
        *,
        work_item: AgentWorkItem | None = None,
    ) -> DiscoveryExecutionResult:
        started = time.monotonic()
        logger.info(
            "discovery_started run_id=%s work_item_id=%s",
            run.id,
            work_item.id if work_item else None,
        )

        profile = load_candidate_profile(self.settings.candidate_profile_path)
        query = plan_discovery_query(
            profile,
            max_raw_results=self.settings.discovery_max_raw_results,
        )
        run.queries_executed = query_debug_lines(query)
        run.status = DiscoveryRunStatus.RUNNING.value
        self.session.flush()

        providers = self.providers or build_discovery_providers(self.settings)
        provider_names = [getattr(p, "name", type(p).__name__) for p in providers]
        run.providers_used = provider_names

        raw_all: list[RawDiscoveryResult] = []
        errors: list[str] = []
        providers_ok = 0
        providers_failed = 0

        for provider in providers:
            name = getattr(provider, "name", type(provider).__name__)
            try:
                logger.info(
                    "discovery_provider_started run_id=%s provider=%s",
                    run.id,
                    name,
                )
                batch = provider.search(query)
                raw_all.extend(batch)
                providers_ok += 1
                logger.info(
                    "discovery_provider_completed run_id=%s provider=%s count=%s",
                    run.id,
                    name,
                    len(batch),
                )
            except Exception as exc:  # noqa: BLE001
                providers_failed += 1
                errors.append(f"{name}: {type(exc).__name__}")
                logger.warning(
                    "discovery_provider_failed run_id=%s provider=%s error=%s",
                    run.id,
                    name,
                    type(exc).__name__,
                )

        run.raw_result_count = len(raw_all)

        if providers_ok == 0:
            run.status = DiscoveryRunStatus.FAILED.value
            run.error_summary = "; ".join(errors)[:500] or "All providers failed"
            run.completed_at = datetime.now(timezone.utc)
            self.session.flush()
            if work_item:
                self.work_items.mark_completed(
                    work_item.id,
                    output_metadata={
                        "discovery_run_id": run.id,
                        "status": run.status,
                        "surfaced": 0,
                    },
                )
            logger.info(
                "discovery_completed run_id=%s status=FAILED duration_ms=%s",
                run.id,
                int((time.monotonic() - started) * 1000),
            )
            return DiscoveryExecutionResult(
                run=run,
                surfaced=[],
                success=False,
                message=run.error_summary or "FAILED",
            )

        # Filter
        kept: list[RankedDiscoveryCandidate] = []
        for raw in raw_all:
            cand = prefilter_candidate(profile, raw)
            if cand.filtered:
                continue
            kept.append(score_candidate(profile, cand))
        run.filtered_result_count = len(kept)

        # Dedupe within run
        deduped = dedupe_within_run(kept)
        run.deduplicated_result_count = len(deduped)

        # Cross-run + require URL + surface top N
        max_surfaced = self.settings.discovery_max_surfaced_results
        surfaced_rows: list[DiscoveryResult] = []
        for cand in sorted(deduped, key=lambda c: c.discovery_score, reverse=True):
            raw = cand.raw
            url = raw.canonical_url or raw.job_url
            if not url:
                continue
            prior = find_prior_identity(self.session, raw)
            if should_block_resurface(prior):
                continue
            if prior is not None and prior.discovery_run_id == run.id:
                continue
            # Upsert: if prior exists as FILTERED/EXPIRED/NEW from old run, reuse row
            row = self._persist_surfaced(run, cand, prior=prior)
            surfaced_rows.append(row)
            logger.info(
                "discovery_result_surfaced run_id=%s result_id=%s provider=%s score=%s",
                run.id,
                row.id,
                row.provider,
                row.discovery_score,
            )
            if len(surfaced_rows) >= max_surfaced:
                break

        run.surfaced_result_count = len(surfaced_rows)
        if providers_failed > 0:
            run.status = DiscoveryRunStatus.PARTIAL.value
            run.error_summary = "; ".join(errors)[:500]
        else:
            run.status = DiscoveryRunStatus.COMPLETED.value
            run.error_summary = None
        run.completed_at = datetime.now(timezone.utc)
        self.session.flush()

        if work_item:
            self.work_items.mark_completed(
                work_item.id,
                output_metadata={
                    "discovery_run_id": run.id,
                    "status": run.status,
                    "raw": run.raw_result_count,
                    "filtered": run.filtered_result_count,
                    "deduped": run.deduplicated_result_count,
                    "surfaced": run.surfaced_result_count,
                    "providers": provider_names,
                },
            )

        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "discovery_completed run_id=%s status=%s raw=%s filtered=%s "
            "deduped=%s surfaced=%s duration_ms=%s",
            run.id,
            run.status,
            run.raw_result_count,
            run.filtered_result_count,
            run.deduplicated_result_count,
            run.surfaced_result_count,
            duration_ms,
        )
        return DiscoveryExecutionResult(
            run=run,
            surfaced=surfaced_rows,
            success=True,
            message=run.status,
        )

    def _persist_surfaced(
        self,
        run: DiscoveryRun,
        cand: RankedDiscoveryCandidate,
        *,
        prior: DiscoveryResult | None,
    ) -> DiscoveryResult:
        raw = cand.raw
        if prior is not None and prior.status in {
            DiscoveryResultStatus.FILTERED.value,
            DiscoveryResultStatus.EXPIRED.value,
            DiscoveryResultStatus.NEW.value,
        }:
            row = prior
            row.discovery_run_id = run.id
        else:
            row = DiscoveryResult(
                discovery_run_id=run.id,
                provider=raw.provider,
                external_id=raw.external_id,
            )
            self.session.add(row)

        row.source_name = raw.source_name
        row.title = raw.title
        row.company = raw.company
        row.location = raw.location_text
        row.work_arrangement = raw.work_arrangement
        row.salary_min = raw.salary_min
        row.salary_max = raw.salary_max
        row.salary_currency = raw.salary_currency
        row.job_url = raw.job_url
        row.canonical_url = raw.canonical_url or raw.job_url
        row.description_snippet = raw.description_snippet
        row.description_full = raw.description_full
        row.published_at = raw.published_at
        row.discovered_at = raw.discovered_at or datetime.now(timezone.utc)
        row.discovery_score = cand.discovery_score
        row.reason_codes = list(cand.reason_codes)
        row.status = DiscoveryResultStatus.SURFACED.value
        row.raw_metadata = dict(raw.raw_metadata or {})
        row.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return row


def queue_discovery_run(
    session: Session,
    *,
    settings: Settings | None = None,
) -> tuple[DiscoveryRun, AgentWorkItem]:
    """Create DiscoveryRun + DISCOVERY work item. Does not search."""
    settings = settings or get_settings()
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()

    item = AgentWorkItem(
        job_id=None,
        pipeline_id=None,
        discovery_run_id=run.id,
        agent_type=AgentType.DISCOVERY.value,
        task_type=WorkItemTaskType.SEARCH_JOBS.value,
        status=WorkItemStatus.PENDING.value,
        input_metadata={"discovery_run_id": run.id},
        attempt_count=0,
    )
    session.add(item)
    session.flush()
    run.work_item_id = item.id
    session.flush()
    return run, item

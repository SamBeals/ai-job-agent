"""Discovery Agent — search, filter, dedupe, rank, persist. Never authorizes.

Provider/network I/O must not hold an open SQLAlchemy Session/transaction.
"""

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
from app.schemas.candidate import CandidateProfile
from app.schemas.discovery import (
    DiscoveryQuery,
    DiscoveryResultStatus,
    DiscoveryRunStatus,
    RankedDiscoveryCandidate,
    RawDiscoveryResult,
)
from app.services.notifications import NotificationService, NullNotificationService
from app.services.work_item_service import WorkItemService

logger = logging.getLogger(__name__)


class DiscoveryAgentError(Exception):
    """Permanent Discovery failure."""


@dataclass
class ProviderSearchOutcome:
    """Raw provider results collected outside any DB Session."""

    raw_results: list[RawDiscoveryResult] = field(default_factory=list)
    providers_used: list[str] = field(default_factory=list)
    providers_ok: int = 0
    providers_failed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class DiscoveryExecutionResult:
    """DTO — no live ORM instances (safe after Session close)."""

    run_id: int
    status: str
    raw_result_count: int = 0
    filtered_result_count: int = 0
    deduplicated_result_count: int = 0
    surfaced_result_count: int = 0
    providers_used: list[str] = field(default_factory=list)
    surfaced_ids: list[int] = field(default_factory=list)
    success: bool = True
    message: str = ""


def search_providers(
    providers: list[Any],
    query: DiscoveryQuery,
    *,
    run_id: int | None = None,
) -> ProviderSearchOutcome:
    """Call external Discovery providers. Must not receive a SQLAlchemy Session."""
    outcome = ProviderSearchOutcome()
    for provider in providers:
        name = getattr(provider, "name", type(provider).__name__)
        outcome.providers_used.append(name)
        try:
            logger.info(
                "discovery_provider_started run_id=%s provider=%s",
                run_id,
                name,
            )
            batch = provider.search(query)
            outcome.raw_results.extend(batch)
            outcome.providers_ok += 1
            logger.info(
                "discovery_provider_completed run_id=%s provider=%s count=%s",
                run_id,
                name,
                len(batch),
            )
        except Exception as exc:  # noqa: BLE001
            outcome.providers_failed += 1
            outcome.errors.append(f"{name}: {type(exc).__name__}")
            logger.warning(
                "discovery_provider_failed run_id=%s provider=%s error=%s",
                run_id,
                name,
                type(exc).__name__,
            )
    return outcome


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

    def process_work_item_id(self, work_item_id: int) -> DiscoveryExecutionResult:
        """Validate claimed work by ID (reload in this Session), then execute."""
        work_item = self.work_items.get(work_item_id)
        if work_item is None:
            raise DiscoveryAgentError(f"Work item {work_item_id} not found")
        return self.process_work_item(work_item)

    def process_work_item(self, work_item: AgentWorkItem) -> DiscoveryExecutionResult:
        """Legacy ORM entry — prefer process_work_item_id from the worker."""
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
        if not work_item.discovery_run_id:
            raise DiscoveryAgentError("Discovery work item missing DiscoveryRun")

        run_id = int(work_item.discovery_run_id)
        work_item_id = int(work_item.id)
        return self.execute_run_by_id(run_id, work_item_id=work_item_id)

    def execute_run_by_id(
        self,
        run_id: int,
        *,
        work_item_id: int | None = None,
        provider_outcome: ProviderSearchOutcome | None = None,
    ) -> DiscoveryExecutionResult:
        """Full Discovery execution.

        If provider_outcome is None, providers are invoked here — callers that want
        network I/O outside the Session should call search_providers separately and
        pass the outcome in.
        """
        started = time.monotonic()
        logger.info(
            "discovery_started run_id=%s work_item_id=%s",
            run_id,
            work_item_id,
        )

        run = self.session.get(DiscoveryRun, run_id)
        if run is None:
            raise DiscoveryAgentError(f"DiscoveryRun {run_id} not found")

        profile = load_candidate_profile(self.settings.candidate_profile_path)
        query = plan_discovery_query(
            profile,
            max_raw_results=self.settings.discovery_max_raw_results,
        )
        run.queries_executed = query_debug_lines(query)
        run.status = DiscoveryRunStatus.RUNNING.value
        self.session.flush()

        if provider_outcome is None:
            # Tests / direct calls may still search here; worker passes precomputed outcome.
            providers = self.providers or build_discovery_providers(self.settings)
            # Commit/close is the caller's responsibility. Prefer worker path that
            # searches outside the Session; this flush only updates run metadata first.
            self.session.flush()
            provider_outcome = search_providers(providers, query, run_id=run_id)

        return self._finalize_from_provider_outcome(
            run=run,
            work_item_id=work_item_id,
            profile=profile,
            provider_outcome=provider_outcome,
            started=started,
        )

    def mark_run_started(
        self,
        run_id: int,
        *,
        query: DiscoveryQuery,
        provider_names: list[str],
    ) -> None:
        """Short transaction helper: mark run RUNNING before external search."""
        run = self.session.get(DiscoveryRun, run_id)
        if run is None:
            raise DiscoveryAgentError(f"DiscoveryRun {run_id} not found")
        run.status = DiscoveryRunStatus.RUNNING.value
        run.queries_executed = query_debug_lines(query)
        run.providers_used = list(provider_names)
        self.session.flush()

    def finalize_provider_outcome(
        self,
        run_id: int,
        *,
        work_item_id: int | None,
        provider_outcome: ProviderSearchOutcome,
        profile: CandidateProfile | None = None,
    ) -> DiscoveryExecutionResult:
        """Persist filter/dedupe/rank results after providers finished outside the Session."""
        started = time.monotonic()
        run = self.session.get(DiscoveryRun, run_id)
        if run is None:
            raise DiscoveryAgentError(f"DiscoveryRun {run_id} not found")
        profile = profile or load_candidate_profile(self.settings.candidate_profile_path)
        return self._finalize_from_provider_outcome(
            run=run,
            work_item_id=work_item_id,
            profile=profile,
            provider_outcome=provider_outcome,
            started=started,
        )

    def _finalize_from_provider_outcome(
        self,
        *,
        run: DiscoveryRun,
        work_item_id: int | None,
        profile: CandidateProfile,
        provider_outcome: ProviderSearchOutcome,
        started: float,
    ) -> DiscoveryExecutionResult:
        run_id = int(run.id)
        provider_names = list(provider_outcome.providers_used)
        run.providers_used = provider_names
        raw_all = provider_outcome.raw_results
        errors = provider_outcome.errors
        providers_ok = provider_outcome.providers_ok
        providers_failed = provider_outcome.providers_failed

        run.raw_result_count = len(raw_all)

        if providers_ok == 0:
            run.status = DiscoveryRunStatus.FAILED.value
            run.error_summary = "; ".join(errors)[:500] or "All providers failed"
            run.completed_at = datetime.now(timezone.utc)
            self.session.flush()
            if work_item_id is not None:
                self.work_items.mark_completed(
                    work_item_id,
                    output_metadata={
                        "discovery_run_id": run_id,
                        "status": run.status,
                        "surfaced": 0,
                    },
                )
            logger.info(
                "discovery_completed run_id=%s status=FAILED duration_ms=%s",
                run_id,
                int((time.monotonic() - started) * 1000),
            )
            return DiscoveryExecutionResult(
                run_id=run_id,
                status=run.status,
                raw_result_count=run.raw_result_count,
                providers_used=provider_names,
                success=False,
                message=run.error_summary or "FAILED",
            )

        kept: list[RankedDiscoveryCandidate] = []
        for raw in raw_all:
            cand = prefilter_candidate(profile, raw)
            if cand.filtered:
                continue
            kept.append(score_candidate(profile, cand))
        run.filtered_result_count = len(kept)

        deduped = dedupe_within_run(kept)
        run.deduplicated_result_count = len(deduped)

        max_surfaced = self.settings.discovery_max_surfaced_results
        surfaced_ids: list[int] = []
        for cand in sorted(deduped, key=lambda c: c.discovery_score, reverse=True):
            raw = cand.raw
            url = raw.canonical_url or raw.job_url
            if not url:
                continue
            prior = find_prior_identity(self.session, raw)
            if should_block_resurface(prior):
                continue
            if prior is not None and prior.discovery_run_id == run_id:
                continue
            row = self._persist_surfaced(run, cand, prior=prior)
            surfaced_ids.append(int(row.id))
            logger.info(
                "discovery_result_surfaced run_id=%s result_id=%s provider=%s score=%s",
                run_id,
                row.id,
                row.provider,
                row.discovery_score,
            )
            if len(surfaced_ids) >= max_surfaced:
                break

        run.surfaced_result_count = len(surfaced_ids)
        if providers_failed > 0:
            run.status = DiscoveryRunStatus.PARTIAL.value
            run.error_summary = "; ".join(errors)[:500]
        else:
            run.status = DiscoveryRunStatus.COMPLETED.value
            run.error_summary = None
        run.completed_at = datetime.now(timezone.utc)
        self.session.flush()

        if work_item_id is not None:
            self.work_items.mark_completed(
                work_item_id,
                output_metadata={
                    "discovery_run_id": run_id,
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
            run_id,
            run.status,
            run.raw_result_count,
            run.filtered_result_count,
            run.deduplicated_result_count,
            run.surfaced_result_count,
            duration_ms,
        )
        return DiscoveryExecutionResult(
            run_id=run_id,
            status=run.status,
            raw_result_count=run.raw_result_count,
            filtered_result_count=run.filtered_result_count,
            deduplicated_result_count=run.deduplicated_result_count,
            surfaced_result_count=run.surfaced_result_count,
            providers_used=provider_names,
            surfaced_ids=surfaced_ids,
            success=True,
            message=run.status,
        )

    def execute_run(
        self,
        run: DiscoveryRun,
        *,
        work_item: AgentWorkItem | None = None,
    ) -> DiscoveryExecutionResult:
        """Compatibility wrapper used by existing unit tests."""
        work_item_id = int(work_item.id) if work_item is not None else None
        return self.execute_run_by_id(int(run.id), work_item_id=work_item_id)

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
        row.normalized_country = cand.normalized_country or raw.normalized_country
        row.us_work_eligible = (
            cand.us_work_eligible
            if cand.us_work_eligible is not None
            else raw.us_work_eligible
        )
        row.raw_metadata = dict(raw.raw_metadata or {})
        row.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return row


def find_active_discovery_work(session: Session) -> AgentWorkItem | None:
    """Return a PENDING/RUNNING Discovery work item if one already exists."""
    from sqlalchemy import select

    stmt = (
        select(AgentWorkItem)
        .where(
            AgentWorkItem.agent_type == AgentType.DISCOVERY.value,
            AgentWorkItem.task_type == WorkItemTaskType.SEARCH_JOBS.value,
            AgentWorkItem.status.in_(
                [WorkItemStatus.PENDING.value, WorkItemStatus.RUNNING.value]
            ),
        )
        .order_by(AgentWorkItem.id.asc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def queue_discovery_run(
    session: Session,
    *,
    settings: Settings | None = None,
) -> tuple[DiscoveryRun, AgentWorkItem]:
    """Create DiscoveryRun + DISCOVERY work item. Does not search.

    Raises DiscoveryAgentError if a Discovery search is already queued or running.
    """
    settings = settings or get_settings()
    existing = find_active_discovery_work(session)
    if existing is not None:
        raise DiscoveryAgentError(
            f"Active Discovery already exists (work item #{existing.id}, "
            f"status={existing.status}, run=#{existing.discovery_run_id}). "
            "Wait for it to finish or recover stale RUNNING work."
        )

    run = DiscoveryRun(status=DiscoveryRunStatus.QUEUED.value)
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

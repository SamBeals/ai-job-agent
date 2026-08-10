"""Ashby public Job Postings API — JSON per board name, no API key."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.agents.discovery.providers.common import (
    infer_work_arrangement,
    title_matches_discovery_query,
)
from app.agents.discovery.providers.html_utils import strip_html
from app.schemas.discovery import DiscoveryQuery, RawDiscoveryResult

logger = logging.getLogger(__name__)


class AshbyDiscoveryProvider:
    """Fetch published jobs from configured Ashby job-board names.

    Docs: https://developers.ashbyhq.com/docs/public-job-posting-api
    Auth: none for public read. Requires known board names.
    """

    name = "ashby"

    def __init__(
        self,
        *,
        board_tokens: list[str],
        timeout_seconds: float = 15.0,
        max_jobs_per_board: int = 80,
        company_names: dict[str, str] | None = None,
    ) -> None:
        self.board_tokens = [t.strip() for t in board_tokens if t and t.strip()]
        self.timeout_seconds = timeout_seconds
        self.max_jobs_per_board = max_jobs_per_board
        self.company_names = company_names or {}

    def search(self, query: DiscoveryQuery) -> list[RawDiscoveryResult]:
        results: list[RawDiscoveryResult] = []
        last_error: Exception | None = None
        n = max(1, len(self.board_tokens))
        per_board = max(8, min(self.max_jobs_per_board, max(1, query.max_raw_results // n + 4)))
        for board in self.board_tokens:
            try:
                results.extend(self._search_board(board, query, limit=per_board))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "discovery_provider_failed provider=ashby board=%s error=%s",
                    board,
                    type(exc).__name__,
                )
        if not results and last_error is not None and self.board_tokens:
            raise last_error
        return results[: query.max_raw_results]

    def _search_board(
        self, board: str, query: DiscoveryQuery, *, limit: int | None = None
    ) -> list[RawDiscoveryResult]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
        started = time.monotonic()
        logger.info("discovery_provider_started provider=ashby board=%s", board)
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(url, params={"includeCompensation": "true"})
            if response.status_code == 429:
                logger.warning(
                    "discovery_provider_rate_limited provider=ashby board=%s retry_after=%s",
                    board,
                    response.headers.get("Retry-After"),
                )
                response.raise_for_status()
            response.raise_for_status()
            payload = response.json()
        jobs = payload.get("jobs") or []
        company = self.company_names.get(board) or board.replace("-", " ").title()
        out: list[RawDiscoveryResult] = []
        cap = limit if limit is not None else self.max_jobs_per_board
        for job in jobs:
            if len(out) >= cap:
                break
            mapped = self._map_job(job, board=board, company=company)
            if mapped is None:
                continue
            if not title_matches_discovery_query(mapped.title, query):
                continue
            out.append(mapped)
        logger.info(
            "discovery_provider_completed provider=ashby board=%s raw_result_count=%s "
            "normalized_result_count=%s duration_ms=%s",
            board,
            len(jobs),
            len(out),
            int((time.monotonic() - started) * 1000),
        )
        return out

    def _map_job(
        self, job: dict[str, Any], *, board: str, company: str
    ) -> RawDiscoveryResult | None:
        if job.get("isListed") is False:
            return None
        job_id = job.get("id")
        title = (job.get("title") or "").strip()
        job_url = (job.get("jobUrl") or job.get("applyUrl") or "").strip()
        if not job_id or not title or not job_url:
            return None

        location = (job.get("location") or "").strip() or None
        secondary = job.get("secondaryLocations") or []
        if isinstance(secondary, list) and secondary:
            extras = [
                (s.get("location") if isinstance(s, dict) else None) for s in secondary
            ]
            extras = [e for e in extras if e]
            if extras:
                location = ", ".join([location, *extras] if location else extras)

        plain = (job.get("descriptionPlain") or "").strip()
        full = plain or strip_html(job.get("descriptionHtml"), limit=4000)
        snippet = (plain[:400] if plain else strip_html(job.get("descriptionHtml"), limit=400))

        salary_min, salary_max, currency = _compensation(job.get("compensation"))

        published = None
        pub = job.get("publishedAt")
        if isinstance(pub, str) and pub:
            try:
                published = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            except ValueError:
                published = None

        workplace = job.get("workplaceType")
        arrangement = infer_work_arrangement(
            location, title, workplace_hint=str(workplace) if workplace else None
        )
        if job.get("isRemote") is True and arrangement is None:
            arrangement = "remote"

        return RawDiscoveryResult(
            provider=self.name,
            source_name=f"ashby:{board}",
            external_id=f"{board}:{job_id}",
            title=title,
            company=company,
            location_text=location,
            work_arrangement=arrangement,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=currency,
            salary_period="year" if salary_min or salary_max else None,
            description_snippet=snippet or None,
            description_full=full or None,
            job_url=job_url,
            canonical_url=(job.get("jobUrl") or job_url).strip(),
            published_at=published,
            discovered_at=datetime.now(timezone.utc),
            raw_metadata={
                "board": board,
                "ashby_id": job_id,
                "department": job.get("department"),
                "team": job.get("team"),
                "apply_url": job.get("applyUrl"),
                "workplace_type": workplace,
            },
        )


def _compensation(comp: Any) -> tuple[int | None, int | None, str | None]:
    if not isinstance(comp, dict):
        return None, None, None
    for tier in comp.get("compensationTiers") or []:
        if not isinstance(tier, dict):
            continue
        for component in tier.get("components") or []:
            if not isinstance(component, dict):
                continue
            if component.get("compensationType") != "Salary":
                continue
            try:
                mn = component.get("minValue")
                mx = component.get("maxValue")
                currency = component.get("currencyCode") or "USD"
                return (
                    int(mn) if mn is not None else None,
                    int(mx) if mx is not None else None,
                    currency,
                )
            except (TypeError, ValueError):
                continue
    for component in comp.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        if component.get("compensationType") != "Salary":
            continue
        try:
            mn = component.get("minValue")
            mx = component.get("maxValue")
            return (
                int(mn) if mn is not None else None,
                int(mx) if mx is not None else None,
                component.get("currencyCode") or "USD",
            )
        except (TypeError, ValueError):
            continue
    return None, None, None

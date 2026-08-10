"""Greenhouse Job Board API provider — public JSON, no API key."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.agents.discovery.providers.html_utils import strip_html
from app.schemas.discovery import DiscoveryQuery, RawDiscoveryResult

logger = logging.getLogger(__name__)


def _infer_arrangement(location: str | None, title: str | None = None) -> str | None:
    blob = f"{location or ''} {title or ''}".lower()
    if "remote" in blob:
        return "remote"
    if "hybrid" in blob:
        return "hybrid"
    if any(x in blob for x in ("on-site", "onsite", "on site")):
        return "onsite"
    return None


class GreenhouseDiscoveryProvider:
    """Fetch published jobs from configured Greenhouse board tokens.

    Docs: https://developers.greenhouse.io/job-board.html
    Auth: none for read. Requires known board tokens (no global search index).
    """

    name = "greenhouse"

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
        # Sample across boards so early large boards cannot starve later tenants.
        per_board = max(8, min(self.max_jobs_per_board, max(1, query.max_raw_results // n + 4)))
        for token in self.board_tokens:
            try:
                batch = self._search_board(token, query, limit=per_board)
                results.extend(batch)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "discovery_provider_failed provider=greenhouse board=%s error=%s",
                    token,
                    type(exc).__name__,
                )
        if not results and last_error is not None and len(self.board_tokens) > 0:
            # All boards failed — surface as provider failure
            raise last_error
        return results[: query.max_raw_results]

    def _search_board(
        self, token: str, query: DiscoveryQuery, *, limit: int | None = None
    ) -> list[RawDiscoveryResult]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        logger.info("discovery_provider_started provider=greenhouse board=%s", token)
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(url, params={"content": "true"})
            response.raise_for_status()
            payload = response.json()
        jobs = payload.get("jobs") or []
        company = self.company_names.get(token) or token.replace("-", " ").title()
        out: list[RawDiscoveryResult] = []
        cap = limit if limit is not None else self.max_jobs_per_board
        for job in jobs:
            if len(out) >= cap:
                break
            mapped = self._map_job(job, token=token, company=company)
            if mapped is None:
                continue
            if not self._title_matches(mapped.title, query):
                continue
            out.append(mapped)
        logger.info(
            "discovery_provider_completed provider=greenhouse board=%s count=%s",
            token,
            len(out),
        )
        return out

    def _title_matches(self, title: str, query: DiscoveryQuery) -> bool:
        t = title.lower()
        # Soft pre-screen: keep software/engineering-ish titles to cut noise
        if any(bad in t for bad in ("sales", "account executive", "recruiter", "marketing")):
            if query.role_terms and not any(r.lower() in t for r in query.role_terms):
                return False
        if query.role_terms:
            return any(
                term.lower() in t
                or any(w in t for w in term.lower().split() if len(w) > 3)
                for term in query.role_terms
            ) or any(x in t for x in ("software", "engineer", "developer", "backend", "platform"))
        return any(x in t for x in ("software", "engineer", "developer", "backend"))

    def _map_job(self, job: dict[str, Any], *, token: str, company: str) -> RawDiscoveryResult | None:
        job_id = job.get("id")
        title = (job.get("title") or "").strip()
        absolute_url = (job.get("absolute_url") or "").strip()
        if not job_id or not title or not absolute_url:
            return None
        location = None
        loc = job.get("location") or {}
        if isinstance(loc, dict):
            location = loc.get("name")
        content = strip_html(job.get("content"), limit=4000)
        snippet = strip_html(job.get("content"), limit=400)
        updated = job.get("updated_at")
        published = None
        if updated:
            try:
                published = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            except ValueError:
                published = None
        return RawDiscoveryResult(
            provider=self.name,
            source_name=f"greenhouse:{token}",
            external_id=f"{token}:{job_id}",
            title=title,
            company=company,
            location_text=location,
            work_arrangement=_infer_arrangement(location, title),
            description_snippet=snippet,
            description_full=content,
            job_url=absolute_url,
            canonical_url=absolute_url,
            published_at=published,
            discovered_at=datetime.now(timezone.utc),
            raw_metadata={"board_token": token, "greenhouse_id": job_id},
        )

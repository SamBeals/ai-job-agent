"""Lever Postings API provider — public JSON per site slug, no API key."""

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


class LeverDiscoveryProvider:
    """Fetch published jobs from configured Lever site slugs.

    Docs: https://github.com/lever/postings-api
    Auth: none for public read. Requires known site slugs (no global index).
    """

    name = "lever"

    def __init__(
        self,
        *,
        site_tokens: list[str],
        timeout_seconds: float = 15.0,
        max_jobs_per_site: int = 80,
        company_names: dict[str, str] | None = None,
    ) -> None:
        self.site_tokens = [t.strip() for t in site_tokens if t and t.strip()]
        self.timeout_seconds = timeout_seconds
        self.max_jobs_per_site = max_jobs_per_site
        self.company_names = company_names or {}

    def search(self, query: DiscoveryQuery) -> list[RawDiscoveryResult]:
        results: list[RawDiscoveryResult] = []
        last_error: Exception | None = None
        n = max(1, len(self.site_tokens))
        per_site = max(8, min(self.max_jobs_per_site, max(1, query.max_raw_results // n + 4)))
        for site in self.site_tokens:
            try:
                results.extend(self._search_site(site, query, limit=per_site))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "discovery_provider_failed provider=lever site=%s error=%s",
                    site,
                    type(exc).__name__,
                )
        if not results and last_error is not None and self.site_tokens:
            raise last_error
        return results[: query.max_raw_results]

    def _search_site(
        self, site: str, query: DiscoveryQuery, *, limit: int | None = None
    ) -> list[RawDiscoveryResult]:
        url = f"https://api.lever.co/v0/postings/{site}"
        started = time.monotonic()
        logger.info("discovery_provider_started provider=lever site=%s", site)
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(url, params={"mode": "json"})
            if response.status_code == 429:
                logger.warning(
                    "discovery_provider_rate_limited provider=lever site=%s retry_after=%s",
                    site,
                    response.headers.get("Retry-After"),
                )
                response.raise_for_status()
            response.raise_for_status()
            payload = response.json()
        jobs = payload if isinstance(payload, list) else []
        company = self.company_names.get(site) or site.replace("-", " ").title()
        out: list[RawDiscoveryResult] = []
        cap = limit if limit is not None else self.max_jobs_per_site
        for job in jobs:
            if len(out) >= cap:
                break
            mapped = self._map_job(job, site=site, company=company)
            if mapped is None:
                continue
            if not title_matches_discovery_query(mapped.title, query):
                continue
            out.append(mapped)
        logger.info(
            "discovery_provider_completed provider=lever site=%s raw_result_count=%s "
            "normalized_result_count=%s duration_ms=%s",
            site,
            len(jobs),
            len(out),
            int((time.monotonic() - started) * 1000),
        )
        return out

    def _map_job(
        self, job: dict[str, Any], *, site: str, company: str
    ) -> RawDiscoveryResult | None:
        job_id = job.get("id")
        title = (job.get("text") or "").strip()
        hosted = (job.get("hostedUrl") or "").strip()
        apply_url = (job.get("applyUrl") or "").strip()
        url = hosted or apply_url
        if not job_id or not title or not url:
            return None

        categories = job.get("categories") or {}
        location = None
        if isinstance(categories, dict):
            location = categories.get("location")
            all_locs = categories.get("allLocations")
            if isinstance(all_locs, list) and all_locs:
                location = ", ".join(str(x) for x in all_locs if x)

        plain = (job.get("descriptionPlain") or job.get("descriptionBodyPlain") or "").strip()
        html = job.get("description") or job.get("descriptionBody")
        full = plain or strip_html(html, limit=4000)
        snippet = (plain[:400] if plain else strip_html(html, limit=400)) or None

        published = None
        created = job.get("createdAt")
        if isinstance(created, (int, float)):
            try:
                published = datetime.fromtimestamp(created / 1000.0, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                published = None

        return RawDiscoveryResult(
            provider=self.name,
            source_name=f"lever:{site}",
            external_id=f"{site}:{job_id}",
            title=title,
            company=company,
            location_text=location,
            work_arrangement=infer_work_arrangement(
                location, title, workplace_hint=job.get("workplaceType")
            ),
            description_snippet=snippet,
            description_full=full or None,
            job_url=url,
            canonical_url=hosted or url,
            published_at=published,
            discovered_at=datetime.now(timezone.utc),
            raw_metadata={
                "site": site,
                "lever_id": job_id,
                "team": categories.get("team") if isinstance(categories, dict) else None,
                "apply_url": apply_url or None,
                "country": job.get("country"),
            },
        )

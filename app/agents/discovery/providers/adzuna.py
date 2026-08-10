"""Adzuna Jobs API — broad geo search (optional; requires app_id + app_key)."""

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
from app.schemas.discovery import DiscoveryQuery, RawDiscoveryResult

logger = logging.getLogger(__name__)


class AdzunaDiscoveryProvider:
    """Broad US job search via Adzuna.

    Docs: https://developer.adzuna.com/
    Auth: app_id + app_key. Disabled when credentials missing.
    Free tier is limited; attribution required if publishing Adzuna ads publicly.
    """

    name = "adzuna"

    def __init__(
        self,
        *,
        app_id: str,
        app_key: str,
        timeout_seconds: float = 15.0,
        country: str = "us",
        results_per_query: int = 20,
    ) -> None:
        self.app_id = (app_id or "").strip()
        self.app_key = (app_key or "").strip()
        self.timeout_seconds = timeout_seconds
        self.country = country
        self.results_per_query = max(1, min(results_per_query, 50))

    def search(self, query: DiscoveryQuery) -> list[RawDiscoveryResult]:
        if not self.app_id or not self.app_key:
            logger.info("discovery_provider_skipped provider=adzuna reason=missing_credentials")
            return []

        started = time.monotonic()
        logger.info("discovery_provider_started provider=adzuna")
        what_terms = _what_terms(query)
        where_terms = _where_terms(query)
        out: list[RawDiscoveryResult] = []
        seen: set[str] = set()

        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            for where in where_terms:
                for what in what_terms:
                    batch = self._search_once(client, what=what, where=where)
                    for mapped in batch:
                        if mapped.external_id in seen:
                            continue
                        if not title_matches_discovery_query(mapped.title, query):
                            continue
                        seen.add(mapped.external_id)
                        out.append(mapped)
                        if len(out) >= query.max_raw_results:
                            break
                    if len(out) >= query.max_raw_results:
                        break
                if len(out) >= query.max_raw_results:
                    break

        logger.info(
            "discovery_provider_completed provider=adzuna raw_result_count=%s "
            "normalized_result_count=%s duration_ms=%s",
            len(out),
            len(out),
            int((time.monotonic() - started) * 1000),
        )
        return out[: query.max_raw_results]

    def _search_once(
        self, client: httpx.Client, *, what: str, where: str
    ) -> list[RawDiscoveryResult]:
        url = f"https://api.adzuna.com/v1/api/jobs/{self.country}/search/1"
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "results_per_page": self.results_per_query,
            "what": what,
            "where": where,
            "content-type": "application/json",
        }
        response = client.get(url, params=params)
        if response.status_code == 429:
            logger.warning(
                "discovery_provider_rate_limited provider=adzuna retry_after=%s",
                response.headers.get("Retry-After"),
            )
            response.raise_for_status()
        response.raise_for_status()
        # Never log params (contains app_key)
        payload = response.json()
        results = payload.get("results") or []
        return [m for m in (self._map_job(j) for j in results) if m is not None]

    def _map_job(self, job: dict[str, Any]) -> RawDiscoveryResult | None:
        job_id = job.get("id")
        title = (job.get("title") or "").strip()
        company_obj = job.get("company") or {}
        company = (
            company_obj.get("display_name") if isinstance(company_obj, dict) else None
        ) or "Unknown"
        redirect = (job.get("redirect_url") or "").strip()
        if not job_id or not title or not redirect:
            return None

        loc_obj = job.get("location") or {}
        location = None
        if isinstance(loc_obj, dict):
            location = loc_obj.get("display_name") or loc_obj.get("area")
            if isinstance(location, list):
                location = ", ".join(str(x) for x in location)

        description = (job.get("description") or "").strip()
        salary_min = _as_int(job.get("salary_min"))
        salary_max = _as_int(job.get("salary_max"))

        published = None
        created = job.get("created")
        if isinstance(created, str) and created:
            try:
                published = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                published = None

        return RawDiscoveryResult(
            provider=self.name,
            source_name="adzuna",
            external_id=str(job_id),
            title=title,
            company=str(company),
            location_text=location if isinstance(location, str) else None,
            work_arrangement=infer_work_arrangement(
                location if isinstance(location, str) else None, title
            ),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency="USD",
            salary_period="year" if salary_min or salary_max else None,
            description_snippet=description[:400] if description else None,
            description_full=description[:4000] if description else None,
            job_url=redirect,
            canonical_url=redirect,
            published_at=published,
            discovered_at=datetime.now(timezone.utc),
            raw_metadata={
                "adzuna_id": job_id,
                "category": (job.get("category") or {}).get("label")
                if isinstance(job.get("category"), dict)
                else None,
            },
        )


def _as_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _what_terms(query: DiscoveryQuery) -> list[str]:
    roles = [r.strip() for r in (query.role_terms or []) if r and r.strip()]
    picks = []
    for r in roles:
        if any(x in r.lower() for x in ("backend", "software", "java")):
            picks.append(r)
        if len(picks) >= 3:
            break
    return picks or ["Backend Software Engineer", "Software Engineer"]


def _where_terms(query: DiscoveryQuery) -> list[str]:
    locs = [t.strip() for t in (query.location_terms or []) if t and t.strip()]
    out: list[str] = []
    seen: set[str] = set()
    for loc in locs:
        key = loc.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(loc)
        if len(out) >= 4:
            break
    if not out:
        out = ["Chandler, AZ", "Phoenix, AZ"]
    if query.include_remote and "remote" not in seen:
        out.append("United States")
    return out

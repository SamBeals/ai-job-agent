"""The Muse public Jobs API — broad keyword/location search, no API key."""

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
from app.agents.discovery.queries import plan_broad_search_logical_queries
from app.schemas.discovery import DiscoveryQuery, RawDiscoveryResult

logger = logging.getLogger(__name__)

_DEFAULT_CATEGORY = "Software Engineering"
_MAX_PAGES = 3
# Cap remote Muse pages lower than local so local search dominates budget.
_REMOTE_MAX_PAGES = 1


class MuseDiscoveryProvider:
    """Broad search via The Muse public jobs API.

    Docs: https://www.themuse.com/developers/api/v2
    Auth: none for public. Location/category query params supported.
    Limitation: landing URLs are Muse-hosted; structured contents still preserved.
    """

    name = "muse"

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        category: str = _DEFAULT_CATEGORY,
        max_pages: int = _MAX_PAGES,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.category = category
        self.max_pages = max(1, min(max_pages, 5))

    def search(self, query: DiscoveryQuery) -> list[RawDiscoveryResult]:
        started = time.monotonic()
        logical = plan_broad_search_logical_queries(query)
        local_n = sum(1 for q in logical if q["bucket"] == "local")
        remote_n = sum(1 for q in logical if q["bucket"] == "remote")
        logger.info(
            "discovery_provider_started provider=muse local_queries=%s remote_queries=%s",
            local_n,
            remote_n,
        )
        out: list[RawDiscoveryResult] = []
        seen_ids: set[str] = set()

        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                for item in logical:
                    bucket = item.get("bucket") or "local"
                    loc = item.get("location")
                    role = item.get("role")
                    max_pages = self.max_pages if bucket == "local" else min(
                        self.max_pages, _REMOTE_MAX_PAGES
                    )
                    page_jobs = self._search_pages(
                        client,
                        location=loc if isinstance(loc, str) else None,
                        role=role if isinstance(role, str) else None,
                        max_pages=max_pages,
                    )
                    for mapped in page_jobs:
                        if mapped.external_id in seen_ids:
                            continue
                        if not title_matches_discovery_query(mapped.title, query):
                            continue
                        mapped = mapped.model_copy(
                            update={
                                "raw_metadata": {
                                    **(mapped.raw_metadata or {}),
                                    "search_bucket": bucket,
                                }
                            }
                        )
                        seen_ids.add(mapped.external_id)
                        out.append(mapped)
                        if len(out) >= query.max_raw_results:
                            break
                    if len(out) >= query.max_raw_results:
                        break
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                logger.warning(
                    "discovery_provider_rate_limited provider=muse retry_after=%s",
                    exc.response.headers.get("Retry-After"),
                )
            raise

        logger.info(
            "discovery_provider_completed provider=muse raw_result_count=%s "
            "normalized_result_count=%s duration_ms=%s",
            len(out),
            len(out),
            int((time.monotonic() - started) * 1000),
        )
        return out[: query.max_raw_results]

    def _search_pages(
        self,
        client: httpx.Client,
        *,
        location: str | None,
        role: str | None,
        max_pages: int | None = None,
    ) -> list[RawDiscoveryResult]:
        out: list[RawDiscoveryResult] = []
        pages = self.max_pages if max_pages is None else max(1, max_pages)
        for page in range(pages):
            params: dict[str, Any] = {"page": page, "category": self.category}
            if location:
                params["location"] = location
            if role:
                # Muse uses free-text via descending relevance when combined with category
                params["descending"] = "true"
            response = client.get("https://www.themuse.com/api/public/jobs", params=params)
            if response.status_code == 429:
                logger.warning("discovery_provider_rate_limited provider=muse")
                response.raise_for_status()
            response.raise_for_status()
            payload = response.json()
            results = payload.get("results") or []
            if not results:
                break
            for job in results:
                mapped = self._map_job(job)
                if mapped is None:
                    continue
                # Soft role preference when a role query was requested
                if role and role.lower() not in mapped.title.lower():
                    # Keep software/engineer titles anyway; drop unrelated
                    if not any(
                        x in mapped.title.lower()
                        for x in ("software", "engineer", "developer", "backend", "java")
                    ):
                        continue
                out.append(mapped)
            page_count = int(payload.get("page_count") or 0)
            if page + 1 >= page_count:
                break
        return out

    def _map_job(self, job: dict[str, Any]) -> RawDiscoveryResult | None:
        job_id = job.get("id")
        title = (job.get("name") or "").strip()
        company_obj = job.get("company") or {}
        company = (
            company_obj.get("name") if isinstance(company_obj, dict) else None
        ) or "Unknown"
        refs = job.get("refs") or {}
        landing = (refs.get("landing_page") if isinstance(refs, dict) else None) or ""
        landing = landing.strip()
        if not job_id or not title or not landing:
            return None

        locs = job.get("locations") or []
        location_names = [
            (loc.get("name") if isinstance(loc, dict) else None) for loc in locs
        ]
        location_names = [n for n in location_names if n]
        location = ", ".join(location_names) if location_names else None

        contents = job.get("contents") or ""
        full = strip_html(contents, limit=4000) if "<" in contents else contents[:4000]
        snippet = full[:400] if full else None

        published = None
        pub = job.get("publication_date")
        if isinstance(pub, str) and pub:
            try:
                published = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            except ValueError:
                published = None

        return RawDiscoveryResult(
            provider=self.name,
            source_name="muse",
            external_id=str(job_id),
            title=title,
            company=str(company),
            location_text=location,
            work_arrangement=infer_work_arrangement(location, title),
            description_snippet=snippet,
            description_full=full or None,
            job_url=landing,
            canonical_url=landing,
            published_at=published,
            discovered_at=datetime.now(timezone.utc),
            raw_metadata={
                "muse_id": job_id,
                "company_short_name": (
                    company_obj.get("short_name") if isinstance(company_obj, dict) else None
                ),
                "levels": job.get("levels"),
                "categories": job.get("categories"),
            },
        )


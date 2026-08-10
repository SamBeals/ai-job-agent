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
from app.schemas.discovery import DiscoveryQuery, RawDiscoveryResult

logger = logging.getLogger(__name__)

_DEFAULT_CATEGORY = "Software Engineering"
_MAX_PAGES = 3


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
        logger.info("discovery_provider_started provider=muse")
        locations = _location_queries(query)
        role_queries = _role_queries(query)
        out: list[RawDiscoveryResult] = []
        seen_ids: set[str] = set()

        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                for loc in locations:
                    for role in role_queries:
                        page_jobs = self._search_pages(client, location=loc, role=role)
                        for mapped in page_jobs:
                            if mapped.external_id in seen_ids:
                                continue
                            if not title_matches_discovery_query(mapped.title, query):
                                continue
                            seen_ids.add(mapped.external_id)
                            out.append(mapped)
                            if len(out) >= query.max_raw_results:
                                break
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
    ) -> list[RawDiscoveryResult]:
        out: list[RawDiscoveryResult] = []
        for page in range(self.max_pages):
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


def _location_queries(query: DiscoveryQuery) -> list[str | None]:
    """Prefer Phoenix-metro terms from planner; always include US Remote if allowed."""
    preferred = []
    for term in query.location_terms or []:
        t = term.strip()
        if not t:
            continue
        # Muse expects "City, ST" style for best results
        preferred.append(t)
        if len(preferred) >= 4:
            break
    if not preferred:
        preferred = ["Phoenix, AZ", "Chandler, AZ"]
    # Deduplicate while preserving order
    out: list[str | None] = []
    seen: set[str] = set()
    for p in preferred:
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    if query.include_remote:
        out.append(None)  # category-only page for broader remote mix
    return out


def _role_queries(query: DiscoveryQuery) -> list[str | None]:
    roles = [r.strip() for r in (query.role_terms or []) if r and r.strip()]
    if not roles:
        return [None]
    # Muse search is category-primary; use a couple of role hints only
    picks = []
    for r in roles:
        if any(x in r.lower() for x in ("backend", "software", "java")):
            picks.append(r)
        if len(picks) >= 2:
            break
    return picks or [roles[0], None]

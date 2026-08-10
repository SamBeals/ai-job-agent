"""Remotive public remote jobs API — no API key."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.schemas.discovery import DiscoveryQuery, RawDiscoveryResult

logger = logging.getLogger(__name__)


class RemotiveDiscoveryProvider:
    """Fetch remote software jobs from Remotive's public JSON API.

    Endpoint: https://remotive.com/api/remote-jobs?category=software-dev
    No authentication. Complements ATS boards with remote-US opportunities.
    """

    name = "remotive"

    def __init__(self, *, timeout_seconds: float = 15.0, category: str = "software-dev") -> None:
        self.timeout_seconds = timeout_seconds
        self.category = category

    def search(self, query: DiscoveryQuery) -> list[RawDiscoveryResult]:
        if not query.include_remote:
            return []
        url = "https://remotive.com/api/remote-jobs"
        logger.info("discovery_provider_started provider=remotive")
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(url, params={"category": self.category})
            response.raise_for_status()
            payload = response.json()
        jobs = payload.get("jobs") or []
        out: list[RawDiscoveryResult] = []
        for job in jobs:
            mapped = self._map_job(job)
            if mapped is None:
                continue
            if query.role_terms:
                t = mapped.title.lower()
                if not (
                    any(r.lower() in t for r in query.role_terms)
                    or any(x in t for x in ("software", "engineer", "developer", "backend"))
                ):
                    continue
            out.append(mapped)
            if len(out) >= query.max_raw_results:
                break
        logger.info("discovery_provider_completed provider=remotive count=%s", len(out))
        return out

    def _map_job(self, job: dict[str, Any]) -> RawDiscoveryResult | None:
        job_id = job.get("id")
        title = (job.get("title") or "").strip()
        company = (job.get("company_name") or "").strip()
        url = (job.get("url") or "").strip()
        if not job_id or not title or not company or not url:
            return None
        salary = job.get("salary")
        # Remotive salary is often a free-text string — leave numeric null unless clear
        description = job.get("description")
        snippet = None
        if isinstance(description, str) and description.strip():
            from app.agents.discovery.providers.html_utils import strip_html

            snippet = strip_html(description, limit=400)
            full = strip_html(description, limit=4000)
        else:
            full = None
        published = None
        pub = job.get("publication_date")
        if pub:
            try:
                published = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
            except ValueError:
                published = None
        return RawDiscoveryResult(
            provider=self.name,
            source_name="remotive",
            external_id=str(job_id),
            title=title,
            company=company,
            location_text=job.get("candidate_required_location") or "Remote",
            work_arrangement="remote",
            description_snippet=snippet,
            description_full=full,
            job_url=url,
            canonical_url=url,
            published_at=published,
            discovered_at=datetime.now(timezone.utc),
            raw_metadata={"salary_text": salary, "job_type": job.get("job_type")},
        )

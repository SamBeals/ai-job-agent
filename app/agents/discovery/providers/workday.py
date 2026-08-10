"""Workday public career-site CXS provider — structured JSON, no API key.

Research (2026-08-10): public career sites call
  POST https://{host}/wday/cxs/{tenant}/{site}/jobs
with JSON body {appliedFacets, limit, offset, searchText}.
Detail:
  GET https://{host}/wday/cxs/{tenant}/{site}{externalPath}

No official public job-board API; this uses the same unauthenticated JSON
the careers UI loads. No CAPTCHA bypass, proxies, or fingerprint spoofing.
Some tenants return 422 for incorrect site paths — only verified boards
belong in the registry.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
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

_PAGE_LIMIT = 20  # Workday listing pages typically cap near 20
_MAX_PAGES_PER_BOARD = 5
_MAX_DETAIL_FETCHES = 12
_REQ_ID_RE = re.compile(r"_([A-Z]{0,3}\d{3,}|JR\d+|R-?\d+)(?:/|$)", re.I)


@dataclass(frozen=True)
class WorkdayBoard:
    company: str
    host: str
    tenant: str
    site: str
    metro: str | None = None

    @property
    def key(self) -> str:
        return f"{self.host}/{self.tenant}/{self.site}"


class WorkdayDiscoveryProvider:
    """Fetch published jobs from configured Workday public career sites."""

    name = "workday"

    def __init__(
        self,
        *,
        boards: list[WorkdayBoard],
        timeout_seconds: float = 15.0,
        max_jobs_per_board: int = 40,
        max_pages_per_board: int = _MAX_PAGES_PER_BOARD,
        fetch_details: bool = True,
        max_detail_fetches: int = _MAX_DETAIL_FETCHES,
    ) -> None:
        self.boards = [b for b in boards if b.host and b.tenant and b.site]
        self.timeout_seconds = timeout_seconds
        self.max_jobs_per_board = max(1, max_jobs_per_board)
        self.max_pages_per_board = max(1, min(max_pages_per_board, 10))
        self.fetch_details = fetch_details
        self.max_detail_fetches = max(0, max_detail_fetches)

    def search(self, query: DiscoveryQuery) -> list[RawDiscoveryResult]:
        results: list[RawDiscoveryResult] = []
        last_error: Exception | None = None
        n = max(1, len(self.boards))
        per_board = max(
            5,
            min(self.max_jobs_per_board, max(1, query.max_raw_results // n + 2)),
        )
        for board in self.boards:
            try:
                batch = self._search_board(board, query, limit=per_board)
                results.extend(batch)
                logger.info(
                    "discovery_workday_board_stats company=%s host=%s "
                    "normalized=%s",
                    board.company,
                    board.host,
                    len(batch),
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "discovery_provider_failed provider=workday board=%s error=%s",
                    board.key,
                    type(exc).__name__,
                )
        if not results and last_error is not None and self.boards:
            raise last_error
        return results[: query.max_raw_results]

    def _search_board(
        self, board: WorkdayBoard, query: DiscoveryQuery, *, limit: int
    ) -> list[RawDiscoveryResult]:
        started = time.monotonic()
        logger.info(
            "discovery_provider_started provider=workday board=%s", board.key
        )
        search_terms = _search_texts(query, board)
        seen_paths: set[str] = set()
        listings: list[dict[str, Any]] = []

        with httpx.Client(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "ai-job-agent-discovery/3.5",
            },
        ) as client:
            for term in search_terms:
                if len(listings) >= limit:
                    break
                page_listings = self._paginate(
                    client, board, search_text=term, limit=limit - len(listings)
                )
                for row in page_listings:
                    path = (row.get("externalPath") or "").strip()
                    if not path or path in seen_paths:
                        continue
                    seen_paths.add(path)
                    listings.append(row)
                    if len(listings) >= limit:
                        break

            out: list[RawDiscoveryResult] = []
            detail_fetches = 0
            for row in listings:
                if len(out) >= limit:
                    break
                title = (row.get("title") or "").strip()
                if not title:
                    continue
                # Soft title filter before spending a detail request
                if not title_matches_discovery_query(title, query):
                    # Keep broad engineer/developer titles for Workday noise
                    if not any(
                        x in title.lower()
                        for x in ("software", "engineer", "developer", "backend", "java")
                    ):
                        continue

                detail: dict[str, Any] | None = None
                if self.fetch_details and detail_fetches < self.max_detail_fetches:
                    detail = self._fetch_detail(client, board, row.get("externalPath") or "")
                    detail_fetches += 1

                mapped = self._map_job(row, board=board, detail=detail)
                if mapped is None:
                    continue
                if not title_matches_discovery_query(mapped.title, query):
                    continue
                out.append(mapped)

        logger.info(
            "discovery_provider_completed provider=workday board=%s "
            "raw_result_count=%s normalized_result_count=%s duration_ms=%s",
            board.key,
            len(listings),
            len(out),
            int((time.monotonic() - started) * 1000),
        )
        return out

    def _paginate(
        self,
        client: httpx.Client,
        board: WorkdayBoard,
        *,
        search_text: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        url = f"https://{board.host}/wday/cxs/{board.tenant}/{board.site}/jobs"
        out: list[dict[str, Any]] = []
        offset = 0
        for _ in range(self.max_pages_per_board):
            if len(out) >= limit:
                break
            page_size = min(_PAGE_LIMIT, limit - len(out))
            payload = {
                "appliedFacets": {},
                "limit": page_size,
                "offset": offset,
                "searchText": search_text,
            }
            response = client.post(url, json=payload)
            if response.status_code == 429:
                logger.warning(
                    "discovery_provider_rate_limited provider=workday board=%s "
                    "retry_after=%s",
                    board.key,
                    response.headers.get("Retry-After"),
                )
                response.raise_for_status()
            if response.status_code >= 400:
                response.raise_for_status()
            try:
                data = response.json()
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError("workday_malformed_json") from exc
            if not isinstance(data, dict):
                raise RuntimeError("workday_malformed_payload")
            posts = data.get("jobPostings") or []
            if not isinstance(posts, list) or not posts:
                break
            for p in posts:
                if isinstance(p, dict):
                    out.append(p)
            if len(posts) < page_size:
                break
            offset += len(posts)
            total = data.get("total")
            if isinstance(total, int) and offset >= total:
                break
        return out[:limit]

    def _fetch_detail(
        self, client: httpx.Client, board: WorkdayBoard, external_path: str
    ) -> dict[str, Any] | None:
        path = (external_path or "").strip()
        if not path:
            return None
        if not path.startswith("/"):
            path = "/" + path
        url = f"https://{board.host}/wday/cxs/{board.tenant}/{board.site}{path}"
        try:
            response = client.get(url)
            if response.status_code == 429:
                logger.warning(
                    "discovery_provider_rate_limited provider=workday board=%s detail",
                    board.key,
                )
                return None
            if response.status_code >= 400:
                return None
            data = response.json()
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001
            return None
        return None

    def _map_job(
        self,
        listing: dict[str, Any],
        *,
        board: WorkdayBoard,
        detail: dict[str, Any] | None,
    ) -> RawDiscoveryResult | None:
        title = (listing.get("title") or "").strip()
        path = (listing.get("externalPath") or "").strip()
        if not title or not path:
            return None
        if not path.startswith("/"):
            path = "/" + path

        info = (detail or {}).get("jobPostingInfo") if detail else None
        if not isinstance(info, dict):
            info = {}

        location = (
            (info.get("location") if isinstance(info.get("location"), str) else None)
            or listing.get("locationsText")
            or None
        )
        if isinstance(location, str):
            location = location.strip() or None

        description_html = info.get("jobDescription") if isinstance(info.get("jobDescription"), str) else None
        full = strip_html(description_html, limit=8000) if description_html else None
        snippet = full[:400] if full else None

        external_url = info.get("externalUrl") if isinstance(info.get("externalUrl"), str) else None
        canonical = external_url or f"https://{board.host}/{board.site}{path}"
        # Prefer en-US style public URL when path looks like /job/...
        if not external_url:
            canonical = f"https://{board.host}/{board.site}{path}"

        req_id = None
        for key in ("jobReqId", "requisitionId"):
            val = info.get(key)
            if isinstance(val, str) and val.strip():
                req_id = val.strip()
                break
        if not req_id:
            m = _REQ_ID_RE.search(path)
            if m:
                req_id = m.group(1)

        posting_id = info.get("id") or info.get("jobPostingId")
        external_id = str(posting_id or req_id or path)

        arrangement = infer_work_arrangement(location, title)
        remote_type = info.get("remoteType")
        if isinstance(remote_type, str) and remote_type.strip():
            rt = remote_type.lower()
            if "remote" in rt:
                arrangement = "remote"
            elif "hybrid" in rt:
                arrangement = "hybrid"

        published = _parse_date(info.get("startDate") or info.get("postedOn") or listing.get("postedOn"))

        salary_min = salary_max = None
        salary_currency = None
        # Workday rarely exposes compensation on public CXS; leave unknown.

        return RawDiscoveryResult(
            provider=self.name,
            source_name=board.company,
            external_id=external_id,
            title=title,
            company=board.company,
            location_text=location,
            work_arrangement=arrangement,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            salary_period=None,
            description_snippet=snippet,
            description_full=full,
            job_url=canonical,
            canonical_url=canonical,
            published_at=published,
            discovered_at=datetime.now(timezone.utc),
            raw_metadata={
                "workday_host": board.host,
                "workday_tenant": board.tenant,
                "workday_site": board.site,
                "external_path": path,
                "requisition_id": req_id,
                "job_posting_id": posting_id,
                "posted_on_list": listing.get("postedOn"),
                "time_type": info.get("timeType"),
                "remote_type": info.get("remoteType"),
                "metro_hint": board.metro,
                "bullet_fields": listing.get("bulletFields"),
            },
        )


def _search_texts(query: DiscoveryQuery, board: WorkdayBoard) -> list[str]:
    """Build a small set of searchText values — local-biased when metro + prefs exist."""
    role = "Software Engineer"
    for r in query.role_terms or []:
        t = (r or "").strip()
        if not t:
            continue
        if any(x in t.lower() for x in ("software", "backend", "java", "platform")):
            role = t
            break
        role = t

    terms: list[str] = []
    # Local-first: prefer preferred-metro search when board is metro-tagged
    if board.metro and query.local_location_terms:
        loc = query.local_location_terms[0]
        # Use state/city token from preference without hardcoding Phoenix
        city = loc.split(",")[0].strip()
        if city:
            terms.append(f"{role} {city}")
        # Also try the state abbreviation if present
        if "," in loc:
            state = loc.split(",", 1)[1].strip()
            if state:
                terms.append(f"{role} {state}")
    terms.append(role)

    # Dedupe preserve order
    out: list[str] = []
    seen: set[str] = set()
    for t in terms:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= 2:
            break
    return out or [role]


def _parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # millis epoch uncommon; skip ambiguous
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    # "Posted 30+ Days Ago" style from listing — not a real date
    if not re.match(r"^\d{4}-\d{2}-\d{2}", raw) and "T" not in raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None

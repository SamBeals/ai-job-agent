"""Oracle Recruiting Candidate Experience provider — public CE JSON, no API key.

Research (2026-08-10): public career SPAs call
  GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
      ?onlyData=true&expand=requisitionList.workLocation,...
      &finder=findReqs;siteNumber={site},keyword=...,limit=...,offset=...
Detail:
  GET .../recruitingCEJobRequisitionDetails?finder=ById;Id={id},siteNumber={site}

Canonical job URL (branded host):
  {career_base_url}/en/sites/{site_path}/job/{id}

No CAPTCHA bypass, proxies, or fingerprint spoofing. Only verified boards
belong in the registry. See docs/oracle-recruiting-research.md.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
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

_PAGE_LIMIT = 25
_MAX_PAGES_PER_BOARD = 5
_MAX_DETAIL_FETCHES = 12
_EXPAND = (
    "requisitionList.workLocation,"
    "requisitionList.otherWorkLocations,"
    "requisitionList.secondaryLocations"
)


@dataclass(frozen=True)
class OracleBoard:
    company: str
    host: str
    site_number: str
    site_path: str
    career_base_url: str
    metro: str | None = None
    location_facet_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        return f"{self.host}/{self.site_number}/{self.site_path}"


class OracleRecruitingDiscoveryProvider:
    """Fetch published jobs from configured Oracle CE public career sites."""

    name = "oracle"

    def __init__(
        self,
        *,
        boards: list[OracleBoard],
        timeout_seconds: float = 15.0,
        max_jobs_per_board: int = 40,
        max_pages_per_board: int = _MAX_PAGES_PER_BOARD,
        fetch_details: bool = True,
        max_detail_fetches: int = _MAX_DETAIL_FETCHES,
    ) -> None:
        self.boards = [
            b
            for b in boards
            if b.host and b.site_number and b.site_path and b.career_base_url
        ]
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
                    "discovery_oracle_board_stats company=%s host=%s normalized=%s",
                    board.company,
                    board.host,
                    len(batch),
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "discovery_provider_failed provider=oracle board=%s error=%s",
                    board.key,
                    type(exc).__name__,
                )
        if not results and last_error is not None and self.boards:
            raise last_error
        return results[: query.max_raw_results]

    def _search_board(
        self, board: OracleBoard, query: DiscoveryQuery, *, limit: int
    ) -> list[RawDiscoveryResult]:
        started = time.monotonic()
        logger.info(
            "discovery_provider_started provider=oracle board=%s", board.key
        )
        search_plans = _search_plans(query, board)
        seen_ids: set[str] = set()
        listings: list[dict[str, Any]] = []

        with httpx.Client(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": "ai-job-agent-discovery/3.6",
            },
        ) as client:
            for plan in search_plans:
                if len(listings) >= limit:
                    break
                page_listings = self._paginate(
                    client,
                    board,
                    keyword=plan["keyword"],
                    location_facet_id=plan.get("location_facet_id"),
                    limit=limit - len(listings),
                )
                for row in page_listings:
                    rid = str(row.get("Id") or "").strip()
                    if not rid or rid in seen_ids:
                        continue
                    seen_ids.add(rid)
                    listings.append(row)
                    if len(listings) >= limit:
                        break

            out: list[RawDiscoveryResult] = []
            detail_fetches = 0
            for row in listings:
                if len(out) >= limit:
                    break
                title = (row.get("Title") or "").strip()
                if not title:
                    continue
                if not title_matches_discovery_query(title, query):
                    if not any(
                        x in title.lower()
                        for x in (
                            "software",
                            "engineer",
                            "developer",
                            "backend",
                            "java",
                            "platform",
                        )
                    ):
                        continue

                detail: dict[str, Any] | None = None
                if self.fetch_details and detail_fetches < self.max_detail_fetches:
                    detail = self._fetch_detail(
                        client, board, str(row.get("Id") or "")
                    )
                    detail_fetches += 1

                mapped = self._map_job(row, board=board, detail=detail)
                if mapped is None:
                    continue
                if not title_matches_discovery_query(mapped.title, query):
                    continue
                out.append(mapped)

        logger.info(
            "discovery_provider_completed provider=oracle board=%s "
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
        board: OracleBoard,
        *,
        keyword: str,
        location_facet_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        url = (
            f"https://{board.host}/hcmRestApi/resources/latest/"
            "recruitingCEJobRequisitions"
        )
        out: list[dict[str, Any]] = []
        offset = 0
        prev_fingerprint: str | None = None
        for _ in range(self.max_pages_per_board):
            if len(out) >= limit:
                break
            page_size = min(_PAGE_LIMIT, limit - len(out))
            finder_parts = [
                f"siteNumber={board.site_number}",
                f"keyword={keyword}",
                f"limit={page_size}",
                f"offset={offset}",
            ]
            if location_facet_id:
                finder_parts.append(f"selectedLocationsFacet={location_facet_id}")
                finder_parts.append("lastSelectedFacet=LOCATIONS")
            finder = "findReqs;" + ",".join(finder_parts)
            response = client.get(
                url,
                params={
                    "onlyData": "true",
                    "expand": _EXPAND,
                    "finder": finder,
                },
            )
            if response.status_code == 429:
                logger.warning(
                    "discovery_provider_rate_limited provider=oracle board=%s "
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
                raise RuntimeError("oracle_malformed_json") from exc
            if not isinstance(data, dict):
                raise RuntimeError("oracle_malformed_payload")
            items = data.get("items")
            if not isinstance(items, list) or not items:
                break
            search_item = items[0]
            if not isinstance(search_item, dict):
                raise RuntimeError("oracle_malformed_search_item")
            posts = search_item.get("requisitionList") or []
            if not isinstance(posts, list) or not posts:
                break
            ids = [str(p.get("Id") or "") for p in posts if isinstance(p, dict)]
            fingerprint = "|".join(ids)
            if fingerprint and fingerprint == prev_fingerprint:
                logger.warning(
                    "discovery_oracle_duplicate_page board=%s offset=%s",
                    board.key,
                    offset,
                )
                break
            prev_fingerprint = fingerprint
            for p in posts:
                if isinstance(p, dict):
                    out.append(p)
            total = search_item.get("TotalJobsCount")
            offset += len(posts)
            if isinstance(total, int) and offset >= total:
                break
            if len(posts) < page_size:
                break
        return out[:limit]

    def _fetch_detail(
        self, client: httpx.Client, board: OracleBoard, requisition_id: str
    ) -> dict[str, Any] | None:
        rid = (requisition_id or "").strip()
        if not rid:
            return None
        url = (
            f"https://{board.host}/hcmRestApi/resources/latest/"
            "recruitingCEJobRequisitionDetails"
        )
        try:
            response = client.get(
                url,
                params={
                    "onlyData": "true",
                    "finder": f"ById;Id={rid},siteNumber={board.site_number}",
                },
            )
            if response.status_code == 429:
                logger.warning(
                    "discovery_provider_rate_limited provider=oracle board=%s detail",
                    board.key,
                )
                return None
            if response.status_code >= 400:
                return None
            data = response.json()
            if not isinstance(data, dict):
                return None
            items = data.get("items")
            if isinstance(items, list) and items and isinstance(items[0], dict):
                return items[0]
        except Exception:  # noqa: BLE001
            return None
        return None

    def _map_job(
        self,
        listing: dict[str, Any],
        *,
        board: OracleBoard,
        detail: dict[str, Any] | None,
    ) -> RawDiscoveryResult | None:
        src = detail if isinstance(detail, dict) and detail else listing
        title = (src.get("Title") or listing.get("Title") or "").strip()
        rid = str(listing.get("Id") or src.get("Id") or "").strip()
        if not title or not rid:
            return None

        location = src.get("PrimaryLocation") or listing.get("PrimaryLocation")
        if isinstance(location, str):
            location = location.strip() or None
        else:
            location = None

        country = src.get("PrimaryLocationCountry") or listing.get(
            "PrimaryLocationCountry"
        )
        if isinstance(country, str):
            country = country.strip() or None
        else:
            country = None

        workplace = src.get("WorkplaceType") or listing.get("WorkplaceType")
        workplace_code = src.get("WorkplaceTypeCode") or listing.get(
            "WorkplaceTypeCode"
        )
        arrangement = infer_work_arrangement(
            location,
            title,
            workplace_hint=(
                str(workplace)
                if workplace
                else (str(workplace_code) if workplace_code else None)
            ),
        )

        description_html = _compose_description(src, listing)
        full = strip_html(description_html, limit=8000) if description_html else None
        snippet = full[:400] if full else None

        base = board.career_base_url.rstrip("/")
        canonical = f"{base}/en/sites/{board.site_path}/job/{rid}"

        published = _parse_date(src.get("PostedDate") or listing.get("PostedDate"))

        return RawDiscoveryResult(
            provider=self.name,
            source_name=board.company,
            external_id=rid,
            title=title,
            company=board.company,
            location_text=location,
            work_arrangement=arrangement,
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            salary_period=None,
            description_snippet=snippet,
            description_full=full,
            job_url=canonical,
            canonical_url=canonical,
            published_at=published,
            discovered_at=datetime.now(timezone.utc),
            raw_metadata={
                "oracle_host": board.host,
                "oracle_site_number": board.site_number,
                "oracle_site_path": board.site_path,
                "career_base_url": board.career_base_url,
                "requisition_id": rid,
                "primary_location_country": country,
                "workplace_type": workplace,
                "workplace_type_code": workplace_code,
                "geography_id": src.get("GeographyId") or listing.get("GeographyId"),
                "posted_date": src.get("PostedDate") or listing.get("PostedDate"),
                "metro_hint": board.metro,
                "application_url": canonical,
            },
        )


def _compose_description(
    detail: dict[str, Any], listing: dict[str, Any]
) -> str | None:
    parts: list[str] = []
    for key in (
        "ExternalDescriptionStr",
        "ExternalResponsibilitiesStr",
        "ExternalQualificationsStr",
        "ShortDescriptionStr",
    ):
        for src in (detail, listing):
            val = src.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
                break
    if not parts:
        return None
    # Dedupe identical blocks
    seen: set[str] = set()
    uniq: list[str] = []
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        uniq.append(p)
    return "\n\n".join(uniq)


def _search_plans(query: DiscoveryQuery, board: OracleBoard) -> list[dict[str, str | None]]:
    """Bounded keyword (+ optional location facet) plans — retrieval only."""
    role = "Software Engineer"
    for r in query.role_terms or []:
        t = (r or "").strip()
        if not t:
            continue
        if any(x in t.lower() for x in ("software", "backend", "java", "platform")):
            role = t
            break
        role = t

    plans: list[dict[str, str | None]] = []
    facet = board.location_facet_ids[0] if board.location_facet_ids else None

    if board.metro and query.local_location_terms:
        city = query.local_location_terms[0].split(",")[0].strip()
        if city:
            plans.append({"keyword": f"{role} {city}", "location_facet_id": None})
        if facet:
            plans.append({"keyword": role, "location_facet_id": facet})

    plans.append({"keyword": role, "location_facet_id": None})

    out: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for p in plans:
        key = f"{p['keyword']}|{p.get('location_facet_id') or ''}"
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= 2:
            break
    return out or [{"keyword": role, "location_facet_id": None}]


def _parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    # Oracle CE often returns YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        try:
            return datetime(
                int(raw[0:4]), int(raw[5:7]), int(raw[8:10]), tzinfo=timezone.utc
            )
        except ValueError:
            return None
    if not re.match(r"^\d{4}-\d{2}-\d{2}", raw) and "T" not in raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None

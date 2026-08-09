"""Safe HTTP fetch for job posting URLs."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.agents.scout.ingestion.models import FetchError, UnsafeURLError
from app.agents.scout.ingestion.url_safety import assert_redirect_target_safe, validate_public_http_url
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchedPage:
    final_url: str
    content_type: str
    text: str
    status_code: int


def fetch_job_page(url: str, *, settings: Settings | None = None, client: httpx.Client | None = None) -> FetchedPage:
    """Fetch a job page with SSRF checks, size limits, and redirect revalidation."""
    settings = settings or get_settings()
    safe_url = validate_public_http_url(url, resolve=True)
    timeout = settings.ingestion_http_timeout_seconds
    max_bytes = settings.ingestion_max_response_bytes
    max_redirects = settings.ingestion_max_redirects
    user_agent = settings.ingestion_user_agent

    logger.info("url_fetch_started")

    owns_client = client is None
    client = client or httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
    )

    try:
        current = safe_url
        for _ in range(max_redirects + 1):
            try:
                with client.stream("GET", current) as response:
                    # Manual redirect handling
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("Scout couldn't retrieve that job page (redirect without Location).")
                        next_url = str(httpx.URL(current).join(location))
                        assert_redirect_target_safe(next_url)
                        current = next_url
                        continue

                    if response.status_code in {401, 403, 429}:
                        raise FetchError(
                            "The site blocked automated retrieval. Paste the job description instead."
                        )
                    if response.status_code >= 400:
                        raise FetchError(
                            "Scout couldn't retrieve that job page. You can use PASTE JOB instead."
                        )

                    content_type = (response.headers.get("content-type") or "").lower()
                    if not _looks_like_text(content_type):
                        raise FetchError(
                            "That URL does not look like an HTML/text job posting and cannot be fetched."
                        )

                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise FetchError(
                                "The job page is too large to download safely. Paste the job description instead."
                            )
                        chunks.append(chunk)

                    raw = b"".join(chunks)
                    text = raw.decode(response.charset_encoding or "utf-8", errors="replace")
                    logger.info("url_fetch_success status=%s bytes=%s", response.status_code, total)
                    return FetchedPage(
                        final_url=str(response.url),
                        content_type=content_type,
                        text=text,
                        status_code=response.status_code,
                    )
            except httpx.TimeoutException as exc:
                raise FetchError(
                    "Scout timed out retrieving that job page. You can use PASTE JOB instead."
                ) from exc
            except httpx.HTTPError as exc:
                raise FetchError(
                    "Scout couldn't retrieve that job page. You can use PASTE JOB instead."
                ) from exc
            except UnsafeURLError:
                raise

        raise FetchError("Too many redirects while fetching that job page.")
    finally:
        if owns_client:
            client.close()


def _looks_like_text(content_type: str) -> bool:
    if not content_type:
        return True  # many servers omit content-type
    allowed_prefixes = (
        "text/",
        "application/json",
        "application/ld+json",
        "application/xml",
        "application/xhtml",
    )
    blocked = (
        "application/octet-stream",
        "application/zip",
        "application/pdf",
        "application/x-",
        "image/",
        "video/",
        "audio/",
    )
    if any(content_type.startswith(b) for b in blocked):
        # Allow xhtml
        if "xhtml" in content_type or "xml" in content_type or "json" in content_type:
            return True
        return False
    return any(content_type.startswith(a) for a in allowed_prefixes) or "html" in content_type

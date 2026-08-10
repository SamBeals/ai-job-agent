"""Discovery-layer deduplication + cross-run identity."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.scout.ingestion.url_safety import canonicalize_url
from app.models.discovery import DiscoveryResult
from app.schemas.discovery import DiscoveryResultStatus, RankedDiscoveryCandidate, RawDiscoveryResult


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_identity_key(company: str, title: str, location: str | None) -> str:
    def _norm(s: str) -> str:
        return _NON_ALNUM.sub(" ", s.lower()).strip()

    return "|".join([_norm(company), _norm(title), _norm(location or "")])


_TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gh_src",
    # Do NOT strip gh_jid — some employers use it as the only job identifier in the URL.
    "ref",
    "mc_cid",
    "mc_eid",
    "fbclid",
    "gclid",
}


def normalize_discovery_url(url: str) -> str:
    """Canonicalize URL and drop common tracking params (never rewrite domains)."""
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    try:
        base = canonicalize_url(url)
    except Exception:  # noqa: BLE001
        base = url.strip().rstrip("/")
    parsed = urlparse(base)
    if not parsed.query:
        return base
    kept = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_QUERY_KEYS
    ]
    query = urlencode(kept)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, "")
    )


def canonical_result_url(raw: RawDiscoveryResult) -> str | None:
    url = raw.canonical_url or raw.job_url
    if not url:
        return None
    try:
        return normalize_discovery_url(url)
    except Exception:  # noqa: BLE001
        return url.rstrip("/")


def dedupe_within_run(
    candidates: list[RankedDiscoveryCandidate],
) -> list[RankedDiscoveryCandidate]:
    """Keep highest-scoring unique opportunities within one run."""
    sorted_cands = sorted(candidates, key=lambda c: c.discovery_score, reverse=True)
    seen_urls: set[str] = set()
    seen_provider_ids: set[tuple[str, str]] = set()
    # identity → provider that first claimed this fingerprint
    seen_identity_owner: dict[str, str] = {}
    out: list[RankedDiscoveryCandidate] = []

    for cand in sorted_cands:
        raw = cand.raw
        url = canonical_result_url(raw)
        provider_key = (raw.provider, raw.external_id)
        identity = normalize_identity_key(raw.company, raw.title, raw.location_text)

        if url and url in seen_urls:
            continue
        if provider_key in seen_provider_ids:
            continue
        if identity in seen_identity_owner:
            owner = seen_identity_owner[identity]
            if owner != raw.provider:
                # Cross-provider copy of the same posting (e.g. Muse + Workday).
                continue
            if not url:
                # Same provider without a distinguishing URL — collapse.
                continue
            # Same provider, distinct canonical URL → distinct requisition; keep.
        else:
            seen_identity_owner[identity] = raw.provider

        if url:
            seen_urls.add(url)
        seen_provider_ids.add(provider_key)
        out.append(cand)
    return out


def find_prior_identity(
    session: Session,
    raw: RawDiscoveryResult,
) -> DiscoveryResult | None:
    """Find a previously persisted DiscoveryResult that should not resurface as NEW.

    Previously DISMISSED / SCOUT_REQUESTED / SCOUTED / SURFACED block resurfacing.
    """
    # provider + external_id
    existing = session.scalars(
        select(DiscoveryResult).where(
            DiscoveryResult.provider == raw.provider,
            DiscoveryResult.external_id == raw.external_id,
        )
    ).first()
    if existing is not None:
        return existing

    url = canonical_result_url(raw)
    if url:
        # Match stored canonical or job URL (loose)
        candidates = session.scalars(
            select(DiscoveryResult)
            .where(DiscoveryResult.canonical_url.is_not(None))
            .order_by(DiscoveryResult.id.desc())
            .limit(300)
        ).all()
        for row in candidates:
            stored = row.canonical_url or row.job_url
            if not stored:
                continue
            try:
                if normalize_discovery_url(stored) == url:
                    return row
            except Exception:  # noqa: BLE001
                if stored.rstrip("/") == url:
                    return row

    identity = normalize_identity_key(raw.company, raw.title, raw.location_text)
    rows = session.scalars(
        select(DiscoveryResult).order_by(DiscoveryResult.id.desc()).limit(400)
    ).all()
    for row in rows:
        if normalize_identity_key(row.company, row.title, row.location) == identity:
            return row
    return None


_BLOCK_RESURFACE = {
    DiscoveryResultStatus.DISMISSED.value,
    DiscoveryResultStatus.SCOUT_REQUESTED.value,
    DiscoveryResultStatus.SCOUTED.value,
    DiscoveryResultStatus.SURFACED.value,
    DiscoveryResultStatus.DUPLICATE.value,
}


def should_block_resurface(prior: DiscoveryResult | None) -> bool:
    if prior is None:
        return False
    return prior.status in _BLOCK_RESURFACE

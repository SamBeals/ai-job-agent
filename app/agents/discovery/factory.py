"""Construct configured Discovery providers."""

from __future__ import annotations

from pathlib import Path

from app.agents.discovery.boards import (
    company_map,
    load_discovery_boards,
    merge_tenants,
    tenant_list,
)
from app.agents.discovery.providers.adzuna import AdzunaDiscoveryProvider
from app.agents.discovery.providers.ashby import AshbyDiscoveryProvider
from app.agents.discovery.providers.base import DiscoveryProvider
from app.agents.discovery.providers.fake import FakeDiscoveryProvider
from app.agents.discovery.providers.greenhouse import GreenhouseDiscoveryProvider
from app.agents.discovery.providers.lever import LeverDiscoveryProvider
from app.agents.discovery.providers.muse import MuseDiscoveryProvider
from app.agents.discovery.providers.oracle import (
    OracleBoard,
    OracleRecruitingDiscoveryProvider,
)
from app.agents.discovery.providers.remotive import RemotiveDiscoveryProvider
from app.agents.discovery.providers.workday import WorkdayBoard, WorkdayDiscoveryProvider
from app.config import Settings, get_settings


def build_discovery_providers(
    settings: Settings | None = None,
    *,
    force_fake: bool = False,
) -> list[DiscoveryProvider]:
    """Return live providers for production, or Fake when configured/forced.

    DISCOVERY_PROVIDER:
      - auto (default): all enabled providers with valid config
      - fake: deterministic test provider only
      - comma-separated names: greenhouse,remotive,lever,ashby,workday,oracle,muse,adzuna
    """
    settings = settings or get_settings()
    if force_fake or (settings.discovery_provider or "").strip().lower() == "fake":
        return [FakeDiscoveryProvider()]

    names = [
        p.strip().lower()
        for p in (settings.discovery_provider or "auto").split(",")
        if p.strip()
    ]
    registry = load_discovery_boards(_boards_path(settings))

    if names == ["auto"]:
        wanted = [
            "greenhouse",
            "lever",
            "ashby",
            "workday",
            "oracle",
            "remotive",
            "muse",
            "adzuna",
        ]
    else:
        wanted = names

    providers: list[DiscoveryProvider] = []
    for name in wanted:
        provider = _build_named(name, settings, registry)
        if provider is not None:
            providers.append(provider)

    return providers or [FakeDiscoveryProvider()]


def _boards_path(settings: Settings) -> Path | None:
    raw = (settings.discovery_boards_path or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        # Resolve relative to repo cwd
        path = Path.cwd() / path
    return path


def _build_named(
    name: str,
    settings: Settings,
    registry: dict,
) -> DiscoveryProvider | None:
    timeout = settings.discovery_http_timeout_seconds

    if name == "fake":
        return FakeDiscoveryProvider()

    if name == "greenhouse":
        if not settings.discovery_greenhouse_enabled:
            return None
        entries = merge_tenants(
            registry.get("greenhouse") or [],
            _parse_boards(settings.discovery_greenhouse_boards),
            _parse_company_map(settings.discovery_greenhouse_company_names),
        )
        # Fix ats label for env-only merges
        tokens = tenant_list(entries)
        if not tokens:
            return None
        return GreenhouseDiscoveryProvider(
            board_tokens=tokens,
            timeout_seconds=timeout,
            company_names={
                **_parse_company_map(settings.discovery_greenhouse_company_names),
                **company_map(entries),
            },
        )

    if name == "lever":
        if not settings.discovery_lever_enabled:
            return None
        entries = merge_tenants(
            registry.get("lever") or [],
            _parse_boards(settings.discovery_lever_sites),
            _parse_company_map(settings.discovery_lever_company_names),
        )
        tokens = tenant_list(entries)
        if not tokens:
            return None
        return LeverDiscoveryProvider(
            site_tokens=tokens,
            timeout_seconds=timeout,
            company_names={
                **_parse_company_map(settings.discovery_lever_company_names),
                **company_map(entries),
            },
        )

    if name == "ashby":
        if not settings.discovery_ashby_enabled:
            return None
        entries = merge_tenants(
            registry.get("ashby") or [],
            _parse_boards(settings.discovery_ashby_boards),
            _parse_company_map(settings.discovery_ashby_company_names),
        )
        tokens = tenant_list(entries)
        if not tokens:
            return None
        return AshbyDiscoveryProvider(
            board_tokens=tokens,
            timeout_seconds=timeout,
            company_names={
                **_parse_company_map(settings.discovery_ashby_company_names),
                **company_map(entries),
            },
        )

    if name == "workday":
        if not settings.discovery_workday_enabled:
            return None
        entries = registry.get("workday") or []
        boards = [
            WorkdayBoard(
                company=e.company,
                host=e.host or "",
                tenant=e.tenant,
                site=e.site or "",
                metro=e.metro,
            )
            for e in entries
            if e.host and e.site and e.tenant
        ]
        if not boards:
            return None
        return WorkdayDiscoveryProvider(
            boards=boards,
            timeout_seconds=timeout,
            max_jobs_per_board=int(settings.discovery_workday_max_jobs_per_board),
            max_pages_per_board=int(settings.discovery_workday_max_pages_per_board),
            fetch_details=bool(settings.discovery_workday_fetch_details),
        )

    if name == "oracle":
        if not settings.discovery_oracle_enabled:
            return None
        entries = registry.get("oracle") or []
        boards = [
            OracleBoard(
                company=e.company,
                host=e.host or "",
                site_number=e.tenant,
                site_path=e.site or e.tenant,
                career_base_url=e.career_base_url or "",
                metro=e.metro,
                location_facet_ids=e.location_facet_ids,
            )
            for e in entries
            if e.host and e.tenant and e.site and e.career_base_url
        ]
        if not boards:
            return None
        return OracleRecruitingDiscoveryProvider(
            boards=boards,
            timeout_seconds=timeout,
            max_jobs_per_board=int(settings.discovery_oracle_max_jobs_per_board),
            max_pages_per_board=int(settings.discovery_oracle_max_pages_per_board),
            fetch_details=bool(settings.discovery_oracle_fetch_details),
        )

    if name == "remotive":
        if not (
            settings.discovery_remotive_enabled or settings.discovery_enable_remotive
        ):
            return None
        return RemotiveDiscoveryProvider(timeout_seconds=timeout)

    if name == "muse":
        if not settings.discovery_muse_enabled:
            return None
        return MuseDiscoveryProvider(timeout_seconds=timeout)

    if name == "adzuna":
        if not settings.discovery_adzuna_enabled:
            return None
        app_id = settings.adzuna_app_id or settings.discovery_adzuna_app_id
        app_key = settings.adzuna_app_key or settings.discovery_adzuna_app_key
        if not app_id or not app_key:
            return None
        return AdzunaDiscoveryProvider(
            app_id=app_id,
            app_key=app_key,
            timeout_seconds=timeout,
        )

    return None


def _parse_boards(raw: str) -> list[str]:
    return [b.strip() for b in (raw or "").split(",") if b.strip()]


def _parse_company_map(raw: str) -> dict[str, str]:
    """Format: token:Company Name;token2:Other Co"""
    out: dict[str, str] = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        token, name = part.split(":", 1)
        token, name = token.strip(), name.strip()
        if token and name:
            out[token] = name
    return out

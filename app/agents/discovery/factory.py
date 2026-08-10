"""Construct configured Discovery providers."""

from __future__ import annotations

from app.agents.discovery.providers.base import DiscoveryProvider
from app.agents.discovery.providers.fake import FakeDiscoveryProvider
from app.agents.discovery.providers.greenhouse import GreenhouseDiscoveryProvider
from app.agents.discovery.providers.remotive import RemotiveDiscoveryProvider
from app.config import Settings, get_settings


def build_discovery_providers(
    settings: Settings | None = None,
    *,
    force_fake: bool = False,
) -> list[DiscoveryProvider]:
    """Return live providers for production, or Fake when configured/forced.

    DISCOVERY_PROVIDER:
      - auto (default): greenhouse boards (if configured) + remotive
      - fake: deterministic test provider only
      - greenhouse: greenhouse only
      - remotive: remotive only
      - greenhouse,remotive: both
    """
    settings = settings or get_settings()
    if force_fake or (settings.discovery_provider or "").strip().lower() == "fake":
        return [FakeDiscoveryProvider()]

    names = [
        p.strip().lower()
        for p in (settings.discovery_provider or "auto").split(",")
        if p.strip()
    ]
    if names == ["auto"]:
        providers: list[DiscoveryProvider] = []
        boards = _parse_boards(settings.discovery_greenhouse_boards)
        if boards:
            providers.append(
                GreenhouseDiscoveryProvider(
                    board_tokens=boards,
                    timeout_seconds=settings.discovery_http_timeout_seconds,
                    company_names=_parse_company_map(settings.discovery_greenhouse_company_names),
                )
            )
        if settings.discovery_enable_remotive:
            providers.append(
                RemotiveDiscoveryProvider(
                    timeout_seconds=settings.discovery_http_timeout_seconds,
                )
            )
        if not providers:
            # No boards configured — Remotive alone still finds real remote jobs
            providers.append(
                RemotiveDiscoveryProvider(
                    timeout_seconds=settings.discovery_http_timeout_seconds,
                )
            )
        return providers

    providers = []
    for name in names:
        if name == "fake":
            providers.append(FakeDiscoveryProvider())
        elif name == "greenhouse":
            boards = _parse_boards(settings.discovery_greenhouse_boards)
            if boards:
                providers.append(
                    GreenhouseDiscoveryProvider(
                        board_tokens=boards,
                        timeout_seconds=settings.discovery_http_timeout_seconds,
                        company_names=_parse_company_map(
                            settings.discovery_greenhouse_company_names
                        ),
                    )
                )
        elif name == "remotive":
            providers.append(
                RemotiveDiscoveryProvider(
                    timeout_seconds=settings.discovery_http_timeout_seconds,
                )
            )
    return providers or [FakeDiscoveryProvider()]


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

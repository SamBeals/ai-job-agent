"""Discovery provider protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.schemas.discovery import DiscoveryQuery, RawDiscoveryResult


@runtime_checkable
class DiscoveryProvider(Protocol):
    """External source of job opportunities."""

    name: str

    def search(self, query: DiscoveryQuery) -> list[RawDiscoveryResult]:
        """Return raw opportunities. Must not invent URLs or salaries."""
        ...

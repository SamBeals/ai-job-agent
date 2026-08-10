"""Discovery provider exports."""

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

__all__ = [
    "AdzunaDiscoveryProvider",
    "AshbyDiscoveryProvider",
    "DiscoveryProvider",
    "FakeDiscoveryProvider",
    "GreenhouseDiscoveryProvider",
    "LeverDiscoveryProvider",
    "MuseDiscoveryProvider",
    "OracleBoard",
    "OracleRecruitingDiscoveryProvider",
    "RemotiveDiscoveryProvider",
    "WorkdayBoard",
    "WorkdayDiscoveryProvider",
]

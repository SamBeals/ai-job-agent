"""Discovery domain schemas — provider-neutral opportunity search."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DiscoveryRunStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class DiscoveryResultStatus(str, Enum):
    NEW = "NEW"
    SURFACED = "SURFACED"
    SCOUT_REQUESTED = "SCOUT_REQUESTED"
    SCOUTED = "SCOUTED"
    DISMISSED = "DISMISSED"
    DUPLICATE = "DUPLICATE"
    EXPIRED = "EXPIRED"
    FILTERED = "FILTERED"


class RawDiscoveryResult(BaseModel):
    """Provider-neutral raw opportunity. Unknown fields stay null."""

    provider: str
    source_name: str
    external_id: str
    title: str
    company: str
    location_text: str | None = None
    work_arrangement: str | None = None  # remote | hybrid | onsite | unknown
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    salary_period: str | None = None  # year | hour | unknown
    description_snippet: str | None = None
    description_full: str | None = None
    job_url: str | None = None
    canonical_url: str | None = None
    published_at: datetime | None = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
    # Normalized by Discovery geography (optional; unknown stays null)
    normalized_country: str | None = None
    us_work_eligible: bool | None = None


class DiscoveryQuery(BaseModel):
    """Planned search inputs for providers."""

    role_terms: list[str] = Field(default_factory=list)
    location_terms: list[str] = Field(default_factory=list)
    # Normalized City, ST terms for Type-B local search (profile-derived).
    local_location_terms: list[str] = Field(default_factory=list)
    include_remote: bool = True
    # When True, broad providers spend request budget on local before remote.
    prioritize_local_search: bool = True
    minimum_base_salary: int | None = None
    prefers_backend: bool | None = None
    prefers_software_development: bool | None = None
    excluded_roles: list[str] = Field(default_factory=list)
    max_raw_results: int = 100


class RankedDiscoveryCandidate(BaseModel):
    raw: RawDiscoveryResult
    discovery_score: int = 0
    reason_codes: list[str] = Field(default_factory=list)
    filtered: bool = False
    filter_reason: str | None = None
    us_work_eligible: bool | None = None
    normalized_country: str | None = None

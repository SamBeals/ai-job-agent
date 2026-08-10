"""DiscoveryRun + DiscoveryResult persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database.database import Base
from app.schemas.discovery import DiscoveryResultStatus, DiscoveryRunStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DiscoveryRun(Base):
    """One Discovery Agent search operation."""

    __tablename__ = "discovery_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default=DiscoveryRunStatus.QUEUED.value,
        index=True,
    )
    providers_used: Mapped[Optional[list[Any]]] = mapped_column(
        JSON().with_variant(SQLiteJSON(), "sqlite"),
        nullable=True,
    )
    queries_executed: Mapped[Optional[list[Any]]] = mapped_column(
        JSON().with_variant(SQLiteJSON(), "sqlite"),
        nullable=True,
    )
    raw_result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    filtered_result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deduplicated_result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    previously_seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    surfaced_result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_stats: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON().with_variant(SQLiteJSON(), "sqlite"),
        nullable=True,
    )
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    work_item_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    results = relationship("DiscoveryResult", back_populates="run", lazy="selectin")

    @property
    def status_enum(self) -> DiscoveryRunStatus:
        return DiscoveryRunStatus(self.status)


class DiscoveryResult(Base):
    """A discovered opportunity surfaced (or filtered) for review."""

    __tablename__ = "discovery_results"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "external_id",
            name="uq_discovery_provider_external",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discovery_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("discovery_runs.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    work_arrangement: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    salary_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    job_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    canonical_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True, index=True)
    description_snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_full: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    discovery_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason_codes: Mapped[Optional[list[Any]]] = mapped_column(
        JSON().with_variant(SQLiteJSON(), "sqlite"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default=DiscoveryResultStatus.NEW.value,
        index=True,
    )
    job_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("jobs.id"), nullable=True, index=True
    )
    normalized_country: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    us_work_eligible: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    discord_posted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON().with_variant(SQLiteJSON(), "sqlite"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    run = relationship("DiscoveryRun", back_populates="results")

    @property
    def status_enum(self) -> DiscoveryResultStatus:
        return DiscoveryResultStatus(self.status)

    @property
    def open_url(self) -> str | None:
        return self.canonical_url or self.job_url

"""Ingestion domain models — separate from Scout evaluation confidence."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.job_posting import NormalizedJob


class InputSource(str, Enum):
    FIXTURE = "FIXTURE"
    URL = "URL"
    TEXT = "TEXT"


class ExtractionMethod(str, Enum):
    JSON_LD = "JSON_LD"
    HTML = "HTML"
    TEXT = "TEXT"
    LLM = "LLM"
    MANUAL_FIELDS = "MANUAL_FIELDS"
    FIXTURE = "FIXTURE"


class ExtractionConfidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ExtractionResult(BaseModel):
    """Outcome of fetch/extract/normalize — not a Scout fit judgment."""

    normalized_job: NormalizedJob
    input_source: InputSource
    extraction_method: ExtractionMethod
    extraction_confidence: ExtractionConfidence
    warnings: list[str] = Field(default_factory=list)
    partial_content: bool = False
    extractor_version: str = "2a.5.1"
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    original_url: str | None = None


class IngestionError(Exception):
    """User-facing ingestion failure (safe to show in Discord)."""

    def __init__(self, message: str, *, code: str = "INGESTION_ERROR") -> None:
        self.code = code
        super().__init__(message)


class UnsafeURLError(IngestionError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="UNSAFE_URL")


class FetchError(IngestionError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="FETCH_FAILED")


class ExtractionError(IngestionError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="EXTRACTION_FAILED")

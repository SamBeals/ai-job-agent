"""Job ingestion package — URL / text / fixture → NormalizedJob."""

from app.agents.scout.ingestion.models import (
    ExtractionConfidence,
    ExtractionError,
    ExtractionMethod,
    ExtractionResult,
    FetchError,
    IngestionError,
    InputSource,
    UnsafeURLError,
)
from app.agents.scout.ingestion.service import DISCORD_DESCRIPTION_MAX, FIXTURE_CATALOG, JobIngestionService

__all__ = [
    "DISCORD_DESCRIPTION_MAX",
    "FIXTURE_CATALOG",
    "ExtractionConfidence",
    "ExtractionError",
    "ExtractionMethod",
    "ExtractionResult",
    "FetchError",
    "IngestionError",
    "InputSource",
    "JobIngestionService",
    "UnsafeURLError",
]

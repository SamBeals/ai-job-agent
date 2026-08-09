"""Domain / I/O Pydantic schemas (separate from SQLAlchemy persistence models)."""

from app.schemas.candidate import CandidateProfile
from app.schemas.evaluation import (
    Confidence,
    Recommendation,
    ScoutEvaluation,
)
from app.schemas.job_posting import NormalizedJob
from app.schemas.preferences import JobPreferences

__all__ = [
    "CandidateProfile",
    "Confidence",
    "JobPreferences",
    "NormalizedJob",
    "Recommendation",
    "ScoutEvaluation",
]

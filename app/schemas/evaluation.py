"""Scout evaluation output schemas — qualification ≠ desirability ≠ authorization."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Recommendation(str, Enum):
    """Scout recommendation. Never authorizes resume/application work."""

    STRONG_RECOMMEND = "STRONG_RECOMMEND"
    RECOMMEND = "RECOMMEND"
    MAYBE = "MAYBE"
    DO_NOT_RECOMMEND = "DO_NOT_RECOMMEND"
    HARD_REJECT = "HARD_REJECT"


class Confidence(str, Enum):
    """How much useful structured information supported the evaluation."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class FilterReason(BaseModel):
    """Explainable hard-filter rejection or warning."""

    code: str
    message: str


class HardFilterResult(BaseModel):
    """Deterministic pre-LLM filter outcome."""

    passed: bool
    rejection_reasons: list[FilterReason] = Field(default_factory=list)
    warnings: list[FilterReason] = Field(default_factory=list)


class ScoutEvaluation(BaseModel):
    """Structured Scout judgment for a single job.

    qualification_score: how well the candidate matches the employer.
    desirability_score: how well the job matches known candidate preferences.
    These must never be collapsed into one opaque match score.
    Recommendation does NOT authorize anything.
    """

    job_id: int | None = None
    qualification_score: int = Field(ge=0, le=100)
    desirability_score: int = Field(ge=0, le=100)
    recommendation: Recommendation
    confidence: Confidence
    matching_skills: list[str] = Field(default_factory=list)
    partial_matches: list[str] = Field(default_factory=list)
    missing_required_skills: list[str] = Field(default_factory=list)
    missing_preferred_skills: list[str] = Field(default_factory=list)
    experience_matches: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    dealbreakers: list[str] = Field(default_factory=list)
    qualification_reasoning: list[str] = Field(default_factory=list)
    desirability_reasoning: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    hard_filter: HardFilterResult | None = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    evaluator_version: str = "2a.6"
    evaluator_provider: str = "mock"
    prompt_version: str | None = None
    evaluator_model: str | None = None
    requirement_matches: list[dict[str, Any]] = Field(default_factory=list)
    job_characteristics: dict[str, Any] | None = None
    token_usage: dict[str, Any] | None = None
    source_content_partial: bool = False
    evaluation_fingerprint: str | None = None

    @field_validator("qualification_score", "desirability_score", mode="before")
    @classmethod
    def _clamp_scores(cls, value: Any) -> int:
        score = int(round(float(value)))
        return max(0, min(100, score))

    def summary_reason(self) -> str:
        """Short human-readable summary suitable for Discord / Job.recommendation_reason."""
        lines: list[str] = [
            f"Qualification: {self.qualification_score}/100",
            f"Desirability: {self.desirability_score}/100",
            f"Confidence: {self.confidence.value}",
            f"Recommendation: {self.recommendation.value}",
        ]
        if self.qualification_reasoning:
            lines.append("")
            lines.append("Why qualification:")
            for item in self.qualification_reasoning[:5]:
                lines.append(f"- {item}")
        if self.desirability_reasoning:
            lines.append("")
            lines.append("Why desirability:")
            for item in self.desirability_reasoning[:5]:
                lines.append(f"- {item}")
        if self.uncertainties:
            lines.append("")
            lines.append("Uncertainties:")
            for item in self.uncertainties[:4]:
                lines.append(f"- {item}")
        return "\n".join(lines)

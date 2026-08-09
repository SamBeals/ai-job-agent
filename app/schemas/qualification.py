"""Structured qualification models — LLM advisory output, not authorization."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.evidence import EvidenceStrength


class RequirementCategory(str, Enum):
    SKILL = "SKILL"
    EXPERIENCE = "EXPERIENCE"
    EDUCATION = "EDUCATION"
    CERTIFICATION = "CERTIFICATION"
    DOMAIN = "DOMAIN"
    RESPONSIBILITY = "RESPONSIBILITY"
    OTHER = "OTHER"


class RequirementType(str, Enum):
    REQUIRED = "REQUIRED"
    PREFERRED = "PREFERRED"
    INFERRED_CONTEXT = "INFERRED_CONTEXT"


class MatchLevel(str, Enum):
    STRONG_MATCH = "STRONG_MATCH"
    MATCH = "MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    TRANSFERABLE = "TRANSFERABLE"
    NO_EVIDENCE = "NO_EVIDENCE"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class FocusLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class JobRequirement(BaseModel):
    """A single job requirement extracted from the posting."""

    id: str
    name: str
    category: RequirementCategory = RequirementCategory.SKILL
    requirement_type: RequirementType = RequirementType.REQUIRED
    importance: float = Field(default=1.0, ge=0.0, le=1.0)
    description: str | None = None
    minimum_years: float | None = None


class RequirementMatch(BaseModel):
    """Evidence-grounded comparison of one requirement to the candidate."""

    requirement: JobRequirement
    match_level: MatchLevel
    evidence_strength: EvidenceStrength = EvidenceStrength.UNKNOWN
    candidate_evidence: list[str] = Field(default_factory=list)
    reasoning: str = ""
    confidence: str = "MEDIUM"  # HIGH|MEDIUM|LOW
    contribution_points: float | None = None  # filled by deterministic scorer


class JobCharacteristics(BaseModel):
    """Semantic job traits for deterministic desirability (optional)."""

    development_focus: FocusLevel = FocusLevel.UNKNOWN
    backend_focus: FocusLevel = FocusLevel.UNKNOWN
    frontend_focus: FocusLevel = FocusLevel.UNKNOWN
    support_operations_focus: FocusLevel = FocusLevel.UNKNOWN
    people_management_focus: FocusLevel = FocusLevel.UNKNOWN


class SemanticJobEvaluation(BaseModel):
    """Structured qualification analysis — NOT a ScoutEvaluation / NOT authorization."""

    requirements: list[RequirementMatch] = Field(default_factory=list)
    job_characteristics: JobCharacteristics = Field(default_factory=JobCharacteristics)
    summary: str = ""
    uncertainties: list[str] = Field(default_factory=list)
    overall_confidence: str = "MEDIUM"


class TokenUsage(BaseModel):
    """Lightweight provider usage observability."""

    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None


class QualificationWeights(BaseModel):
    """Configurable scoring weights for aggregating RequirementMatch rows."""

    required_pool_weight: float = 0.80
    preferred_pool_weight: float = 0.20
    match_points: dict[str, float] = Field(
        default_factory=lambda: {
            MatchLevel.STRONG_MATCH.value: 1.0,
            MatchLevel.MATCH.value: 0.85,
            MatchLevel.PARTIAL_MATCH.value: 0.45,
            MatchLevel.TRANSFERABLE.value: 0.35,
            MatchLevel.UNKNOWN.value: 0.25,
            MatchLevel.NO_EVIDENCE.value: 0.0,
            MatchLevel.CONFLICT.value: 0.0,
        }
    )
    listed_skill_cap: float = 0.55
    conflict_penalty: float = 12.0
    missing_required_penalty: float = 10.0
    no_required_skills_base: int = 50

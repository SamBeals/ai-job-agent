"""Evidence strength models for skill and experience matching."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class EvidenceStrength(str, Enum):
    """How strongly a candidate fact supports a claim."""

    PROFESSIONAL_EXPERIENCE = "PROFESSIONAL_EXPERIENCE"
    PROJECT = "PROJECT"
    EDUCATION = "EDUCATION"
    CERTIFICATION = "CERTIFICATION"
    LISTED_SKILL = "LISTED_SKILL"
    UNKNOWN = "UNKNOWN"


# Relative weights used by deterministic matching (not opaque ML scores).
EVIDENCE_WEIGHTS: dict[EvidenceStrength, float] = {
    EvidenceStrength.PROFESSIONAL_EXPERIENCE: 1.0,
    EvidenceStrength.PROJECT: 0.75,
    EvidenceStrength.EDUCATION: 0.55,
    EvidenceStrength.CERTIFICATION: 0.5,
    EvidenceStrength.LISTED_SKILL: 0.45,
    EvidenceStrength.UNKNOWN: 0.0,
}


class SkillEvidence(BaseModel):
    """Evidence that a candidate possesses (or partially matches) a skill."""

    skill: str
    matched_candidate_skill: str | None = None
    strength: EvidenceStrength = EvidenceStrength.UNKNOWN
    source_detail: str | None = None
    notes: str | None = None


class SkillMatchReport(BaseModel):
    """Structured skill match results for a job evaluation."""

    matching_skills: list[SkillEvidence] = Field(default_factory=list)
    partial_matches: list[SkillEvidence] = Field(default_factory=list)
    missing_required_skills: list[str] = Field(default_factory=list)
    missing_preferred_skills: list[str] = Field(default_factory=list)
    experience_matches: list[str] = Field(default_factory=list)

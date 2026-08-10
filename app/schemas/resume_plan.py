"""Structured ResumePlan — Resume Agent output artifact (not a PDF/DOCX)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ResumePlanItem(BaseModel):
    """A claim or emphasis item grounded in verified evidence."""

    text: str
    category: str = "general"  # experience | skill | accomplishment | education | other
    evidence_strength: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    source_detail: str | None = None


class ResumePlan(BaseModel):
    """Tailoring plan produced by Resume Agent — factual integrity required."""

    job_id: int
    pipeline_id: int
    target_title: str
    company: str | None = None
    summary_strategy: str = ""
    priority_experience: list[ResumePlanItem] = Field(default_factory=list)
    priority_skills: list[ResumePlanItem] = Field(default_factory=list)
    priority_accomplishments: list[ResumePlanItem] = Field(default_factory=list)
    requirements_to_emphasize: list[ResumePlanItem] = Field(default_factory=list)
    transferable_experience: list[ResumePlanItem] = Field(default_factory=list)
    secondary_skills: list[ResumePlanItem] = Field(default_factory=list)
    skills_not_to_claim: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    experience_ordering: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_version: str = "3.0.0"
    validation_passed: bool = False
    validation_errors: list[str] = Field(default_factory=list)
    scout_evaluation_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

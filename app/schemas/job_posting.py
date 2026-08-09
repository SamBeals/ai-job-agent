"""Normalized employer job posting schema for Scout evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class NormalizedJob(BaseModel):
    """Normalized representation of a job posting.

    Unknown fields remain null. Parsers must not hallucinate missing salary,
    location, seniority, or other attributes.
    """

    external_id: str | None = None
    source: str = "manual"
    source_url: str | None = None
    company: str
    title: str
    location: str | None = None
    remote_status: str | None = None  # remote | hybrid | onsite | unknown
    employment_type: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    description: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    required_years_experience: float | None = None
    education_requirements: list[str] = Field(default_factory=list)
    seniority: str | None = None
    industry: str | None = None
    company_size: str | None = None
    requires_relocation: bool | None = None
    requires_security_clearance: str | None = None
    date_posted: datetime | None = None
    discovered_at: datetime | None = None

    @field_validator("required_skills", "preferred_skills", "responsibilities", "education_requirements", mode="before")
    @classmethod
    def _none_to_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        return value

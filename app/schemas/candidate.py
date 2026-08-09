"""Candidate profile domain schemas — facts separate from preferences."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.evidence import EvidenceStrength
from app.schemas.preferences import JobPreferences


class VerifiedSkill(BaseModel):
    """A skill with explicit evidence metadata.

    Listed skills are verified as inventory entries. Depth, years, and
    proficiency remain null unless separately supported.
    """

    name: str
    verified: bool = True
    source: str = "resume"
    evidence_type: EvidenceStrength | None = EvidenceStrength.LISTED_SKILL
    proficiency: str | None = None
    years_experience: float | None = None
    last_used: str | None = None
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("skill name must not be empty")
        return cleaned


def _coerce_skill(item: Any) -> VerifiedSkill:
    if isinstance(item, VerifiedSkill):
        return item
    if isinstance(item, str):
        return VerifiedSkill(name=item)
    if isinstance(item, dict):
        return VerifiedSkill.model_validate(item)
    raise TypeError(f"Cannot coerce skill from {type(item)!r}")


class SkillInventory(BaseModel):
    """Verified skill inventory by category."""

    languages: list[VerifiedSkill] = Field(default_factory=list)
    frameworks: list[VerifiedSkill] = Field(default_factory=list)
    cloud_and_infra: list[VerifiedSkill] = Field(default_factory=list)
    databases: list[VerifiedSkill] = Field(default_factory=list)
    practices: list[VerifiedSkill] = Field(default_factory=list)
    other: list[VerifiedSkill] = Field(default_factory=list)

    @field_validator(
        "languages",
        "frameworks",
        "cloud_and_infra",
        "databases",
        "practices",
        "other",
        mode="before",
    )
    @classmethod
    def _coerce_skills(cls, value: Any) -> list[VerifiedSkill]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("skills categories must be lists")
        return [_coerce_skill(item) for item in value]

    def all_skills(self) -> list[VerifiedSkill]:
        return [
            *self.languages,
            *self.frameworks,
            *self.cloud_and_infra,
            *self.databases,
            *self.practices,
            *self.other,
        ]


class CandidateIdentity(BaseModel):
    """Identity fields used for presentation; contact is optional for Scout."""

    full_name: str
    location: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None


class WorkExperience(BaseModel):
    """Verified professional experience entry."""

    company: str
    title: str
    location: str | None = None
    employment_type: str | None = None  # full_time, contract, etc.
    start_date: str  # YYYY-MM
    end_date: str | None = None
    is_current: bool = False
    verified_accomplishments: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)


class Education(BaseModel):
    """Verified education entry. Incomplete degrees must not be marked complete."""

    institution: str
    degree: str
    field: str | None = None
    location: str | None = None
    format: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    graduation_date: str | None = None
    status: str | None = None  # completed | in_progress | unknown
    details: list[str] = Field(default_factory=list)


class Certification(BaseModel):
    """Verified certification. Issuer/dates optional unless known."""

    name: str
    issuer: str | None = None
    date_earned: str | None = None
    credential_id: str | None = None


class Project(BaseModel):
    """Verified personal or side project."""

    name: str
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    url: str | None = None
    verified_outcomes: list[str] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    """Authoritative candidate career profile for Scout / Resume agents.

    Preferences are nested but conceptually separate from career facts.
    """

    schema_version: str = "2a.1"
    identity: CandidateIdentity
    professional_summary: str | None = None
    work_experience: list[WorkExperience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: SkillInventory = Field(default_factory=SkillInventory)
    certifications: list[Certification] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    preferences: JobPreferences = Field(default_factory=JobPreferences)

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_keys(cls, data: Any) -> Any:
        """Accept Phase 1 example keys (personal/work_history/job_preferences)."""
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if "identity" not in normalized and "personal" in normalized:
            normalized["identity"] = normalized.pop("personal")
        if "work_experience" not in normalized and "work_history" in normalized:
            work = []
            for entry in normalized.pop("work_history"):
                if not isinstance(entry, dict):
                    work.append(entry)
                    continue
                item = dict(entry)
                if "company" not in item and "employer" in item:
                    item["company"] = item.pop("employer")
                work.append(item)
            normalized["work_experience"] = work
        if "professional_summary" not in normalized and "summary" in normalized:
            normalized["professional_summary"] = normalized.pop("summary")
        if "preferences" not in normalized and "job_preferences" in normalized:
            prefs = normalized.pop("job_preferences")
            if isinstance(prefs, dict):
                mapped = dict(prefs)
                # Map Phase 1 example preference keys into Phase 2A schema.
                if "target_roles" not in mapped and "target_roles" in normalized:
                    mapped["target_roles"] = normalized.get("target_roles")
                if "remote_preference" not in mapped and "remote" in mapped:
                    mapped["remote_preference"] = mapped.pop("remote")
                if "acceptable_locations" not in mapped and "locations_ok" in mapped:
                    mapped["acceptable_locations"] = mapped.pop("locations_ok")
                if "minimum_base_salary" not in mapped and "salary_min" in mapped:
                    mapped["minimum_base_salary"] = mapped.pop("salary_min")
                if "currency" not in mapped and "salary_currency" in mapped:
                    mapped["currency"] = mapped.pop("salary_currency")
                if "dealbreakers" not in mapped and "avoid" in mapped:
                    mapped["dealbreakers"] = mapped.pop("avoid")
                if "preferred_company_sizes" not in mapped and "company_sizes" in mapped:
                    mapped["preferred_company_sizes"] = mapped.pop("company_sizes")
                # employment_types list → boolean flags when present
                if "employment_types" in mapped and isinstance(mapped["employment_types"], list):
                    types = {str(t).lower() for t in mapped.pop("employment_types")}
                    if mapped.get("full_time_allowed") is None and "full_time" in types:
                        mapped["full_time_allowed"] = True
                    if mapped.get("contract_allowed") is None and "contract" in types:
                        mapped["contract_allowed"] = True
                normalized["preferences"] = mapped
            else:
                normalized["preferences"] = prefs
        # Promote top-level target_roles into preferences if needed
        if "target_roles" in normalized:
            roles = normalized.pop("target_roles")
            prefs = normalized.get("preferences") or {}
            if isinstance(prefs, dict) and prefs.get("target_roles") is None:
                prefs = dict(prefs)
                prefs["target_roles"] = roles
                normalized["preferences"] = prefs
        # Drop documentation-only keys
        normalized.pop("_documentation", None)
        return normalized

    def approximate_years_of_experience(self) -> float | None:
        """Derive approximate YoE from employment dates when possible."""
        from app.agents.scout.dates import approximate_years_from_experience

        return approximate_years_from_experience(self.work_experience)

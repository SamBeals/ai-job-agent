"""Candidate preference schema — UNKNOWN-safe (null ≠ invented default)."""

from __future__ import annotations

from pydantic import BaseModel


class JobPreferences(BaseModel):
    """What the candidate wants from a future job.

    Null / omitted fields mean UNKNOWN — they must not cause hard rejection
    or arbitrary desirability penalties.
    Empty lists mean the candidate explicitly supplied an empty set.
    """

    # Role preferences
    target_roles: list[str] | None = None
    acceptable_roles: list[str] | None = None
    excluded_roles: list[str] | None = None

    # Seniority
    preferred_seniority: list[str] | None = None
    acceptable_seniority: list[str] | None = None
    excluded_seniority: list[str] | None = None

    # Compensation
    minimum_base_salary: int | None = None
    preferred_base_salary: int | None = None
    minimum_total_compensation: int | None = None
    currency: str | None = None

    # Work arrangement
    remote_preference: str | None = None  # primary preferred arrangement label
    # Ordered preference: e.g. ["hybrid", "onsite", "remote"]. Affects ranking, not eligibility.
    work_arrangement_order: list[str] | None = None
    remote_required: bool | None = None
    hybrid_allowed: bool | None = None
    onsite_allowed: bool | None = None

    # Geography
    home_location: str | None = None
    # Ideal / strongest ranking boost (e.g. Chandler). Not a hard whitelist.
    preferred_locations: list[str] | None = None
    # Broader acceptable geography for ranking (e.g. Phoenix Metro). Not a hard whitelist.
    acceptable_locations: list[str] | None = None
    maximum_commute: str | None = None
    relocation_allowed: bool | None = None

    # Work-focus preferences (desirability ranking; not hard filters by default)
    prefers_software_development: bool | None = None
    prefers_backend: bool | None = None

    # Employment
    full_time_allowed: bool | None = None
    contract_allowed: bool | None = None
    contract_to_hire_allowed: bool | None = None

    # Company
    preferred_company_sizes: list[str] | None = None
    excluded_company_sizes: list[str] | None = None
    preferred_industries: list[str] | None = None
    excluded_industries: list[str] | None = None

    # Additional
    dealbreakers: list[str] | None = None
    preferences: list[str] | None = None
    notes: str | None = None

    # Work authorization / clearance (explicit only)
    security_clearance_held: list[str] | None = None
    security_clearance_required_rejected: bool | None = None

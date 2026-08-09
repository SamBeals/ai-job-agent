"""Deterministic hard filters — explainable dealbreakers before LLM judgment."""

from __future__ import annotations

from app.schemas.candidate import CandidateProfile
from app.schemas.evaluation import FilterReason, HardFilterResult
from app.schemas.job_posting import NormalizedJob
from app.schemas.preferences import JobPreferences


def apply_hard_filters(candidate: CandidateProfile, job: NormalizedJob) -> HardFilterResult:
    """Reject only when structured data proves an explicit preference violation.

    Unknown preferences and unknown job fields must not cause rejection.
    """
    prefs = candidate.preferences
    rejections: list[FilterReason] = []
    warnings: list[FilterReason] = []

    _check_salary(prefs, job, rejections, warnings)
    _check_roles(prefs, job, rejections)
    _check_seniority(prefs, job, rejections)
    _check_remote(prefs, job, rejections, warnings)
    _check_location_relocation(prefs, job, rejections, warnings)
    _check_employment_type(prefs, job, rejections, warnings)
    _check_industry(prefs, job, rejections)
    _check_company_size(prefs, job, rejections)
    _check_security_clearance(prefs, job, rejections, warnings)

    return HardFilterResult(
        passed=len(rejections) == 0,
        rejection_reasons=rejections,
        warnings=warnings,
    )


def _check_salary(
    prefs: JobPreferences,
    job: NormalizedJob,
    rejections: list[FilterReason],
    warnings: list[FilterReason],
) -> None:
    minimum = prefs.minimum_base_salary
    if minimum is None:
        if job.salary_min is None and job.salary_max is None:
            warnings.append(
                FilterReason(
                    code="SALARY_UNKNOWN",
                    message="Salary compatibility unknown (no preference minimum and no listed salary).",
                )
            )
        return

    if job.salary_min is None and job.salary_max is None:
        warnings.append(
            FilterReason(
                code="SALARY_UNKNOWN",
                message="Salary compatibility unknown because compensation was not listed.",
            )
        )
        return

    # Use the best available upper bound for the offer
    offer_max = job.salary_max if job.salary_max is not None else job.salary_min
    if offer_max is not None and offer_max < minimum:
        rejections.append(
            FilterReason(
                code="SALARY_BELOW_MINIMUM",
                message=(
                    f"Maximum listed base salary ({offer_max}) is below "
                    f"candidate minimum ({minimum})."
                ),
            )
        )


def _check_roles(
    prefs: JobPreferences,
    job: NormalizedJob,
    rejections: list[FilterReason],
) -> None:
    excluded = prefs.excluded_roles
    if not excluded:
        return
    title = job.title.lower()
    for role in excluded:
        role_l = role.lower().strip()
        if role_l and role_l in title:
            rejections.append(
                FilterReason(
                    code="EXCLUDED_ROLE",
                    message=f"Job title matches excluded role pattern: {role}",
                )
            )
            return


def _check_seniority(
    prefs: JobPreferences,
    job: NormalizedJob,
    rejections: list[FilterReason],
) -> None:
    excluded = prefs.excluded_seniority
    if not excluded or not job.seniority:
        return
    seniority = job.seniority.lower()
    for item in excluded:
        if item.lower() in seniority or seniority in item.lower():
            rejections.append(
                FilterReason(
                    code="EXCLUDED_SENIORITY",
                    message=f"Job seniority '{job.seniority}' is explicitly excluded.",
                )
            )
            return


def _check_remote(
    prefs: JobPreferences,
    job: NormalizedJob,
    rejections: list[FilterReason],
    warnings: list[FilterReason],
) -> None:
    remote_status = (job.remote_status or "").lower().strip()

    if prefs.remote_required is True:
        if not remote_status:
            warnings.append(
                FilterReason(
                    code="REMOTE_STATUS_UNKNOWN",
                    message="Work arrangement compatibility unknown (remote required; job remote status missing).",
                )
            )
            return
        if remote_status in {"onsite", "on-site", "on site", "office"}:
            rejections.append(
                FilterReason(
                    code="REMOTE_REQUIRED",
                    message="Candidate requires remote work; job is on-site.",
                )
            )
            return
        if remote_status == "hybrid" and prefs.hybrid_allowed is False:
            rejections.append(
                FilterReason(
                    code="HYBRID_NOT_ALLOWED",
                    message="Candidate requires remote work and does not allow hybrid.",
                )
            )
            return

    if prefs.onsite_allowed is False and remote_status in {"onsite", "on-site", "on site", "office"}:
        rejections.append(
            FilterReason(
                code="ONSITE_NOT_ALLOWED",
                message="On-site work is not allowed by candidate preferences.",
            )
        )

    if not remote_status and prefs.remote_required is None:
        warnings.append(
            FilterReason(
                code="REMOTE_STATUS_UNKNOWN",
                message="Work arrangement compatibility unknown.",
            )
        )


def _check_location_relocation(
    prefs: JobPreferences,
    job: NormalizedJob,
    rejections: list[FilterReason],
    warnings: list[FilterReason],
) -> None:
    """Relocation can hard-reject only when explicitly prohibited.

    preferred_locations / acceptable_locations are ranking signals for
    desirability — not hard whitelists. Geographic mismatch must not
    eliminate otherwise strong Phoenix-metro or remote opportunities.
    """
    if prefs.relocation_allowed is False and job.requires_relocation is True:
        rejections.append(
            FilterReason(
                code="RELOCATION_PROHIBITED",
                message="Job requires relocation but candidate prohibits relocation.",
            )
        )

    remote_status = (job.remote_status or "").lower()
    if (
        (prefs.preferred_locations or prefs.acceptable_locations)
        and not job.location
        and remote_status not in {"remote"}
        and not remote_status
    ):
        warnings.append(
            FilterReason(
                code="LOCATION_UNCERTAIN",
                message="Location compatibility uncertain (no location or remote status listed).",
            )
        )


def _check_employment_type(
    prefs: JobPreferences,
    job: NormalizedJob,
    rejections: list[FilterReason],
    warnings: list[FilterReason],
) -> None:
    emp = (job.employment_type or "").lower().strip()
    if not emp:
        if any(
            v is not None
            for v in (prefs.full_time_allowed, prefs.contract_allowed, prefs.contract_to_hire_allowed)
        ):
            warnings.append(
                FilterReason(
                    code="EMPLOYMENT_TYPE_UNKNOWN",
                    message="Employment type compatibility unknown.",
                )
            )
        return

    if "full" in emp and prefs.full_time_allowed is False:
        rejections.append(
            FilterReason(
                code="FULL_TIME_NOT_ALLOWED",
                message="Full-time employment is explicitly not allowed.",
            )
        )
    if "contract" in emp and "hire" not in emp and prefs.contract_allowed is False:
        rejections.append(
            FilterReason(
                code="CONTRACT_NOT_ALLOWED",
                message="Contract employment is explicitly not allowed.",
            )
        )
    if "contract" in emp and "hire" in emp and prefs.contract_to_hire_allowed is False:
        rejections.append(
            FilterReason(
                code="CONTRACT_TO_HIRE_NOT_ALLOWED",
                message="Contract-to-hire employment is explicitly not allowed.",
            )
        )


def _check_industry(
    prefs: JobPreferences,
    job: NormalizedJob,
    rejections: list[FilterReason],
) -> None:
    excluded = prefs.excluded_industries
    if not excluded or not job.industry:
        return
    industry = job.industry.lower()
    for item in excluded:
        if item.lower() in industry:
            rejections.append(
                FilterReason(
                    code="EXCLUDED_INDUSTRY",
                    message=f"Industry '{job.industry}' is explicitly excluded.",
                )
            )
            return


def _check_company_size(
    prefs: JobPreferences,
    job: NormalizedJob,
    rejections: list[FilterReason],
) -> None:
    excluded = prefs.excluded_company_sizes
    if not excluded or not job.company_size:
        return
    size = job.company_size.lower()
    for item in excluded:
        if item.lower() == size:
            rejections.append(
                FilterReason(
                    code="EXCLUDED_COMPANY_SIZE",
                    message=f"Company size '{job.company_size}' is explicitly excluded.",
                )
            )
            return


def _check_security_clearance(
    prefs: JobPreferences,
    job: NormalizedJob,
    rejections: list[FilterReason],
    warnings: list[FilterReason],
) -> None:
    required = job.requires_security_clearance
    if not required:
        return
    held = prefs.security_clearance_held
    if held is None:
        if prefs.security_clearance_required_rejected is True:
            rejections.append(
                FilterReason(
                    code="SECURITY_CLEARANCE_REQUIRED",
                    message=f"Job requires security clearance ({required}); candidate rejects clearance roles.",
                )
            )
        else:
            warnings.append(
                FilterReason(
                    code="SECURITY_CLEARANCE_UNKNOWN",
                    message=f"Job requires security clearance ({required}); candidate clearance status unknown.",
                )
            )
        return
    held_l = {h.lower() for h in held}
    if required.lower() not in held_l and "any" not in held_l:
        rejections.append(
            FilterReason(
                code="SECURITY_CLEARANCE_MISSING",
                message=f"Job requires security clearance ({required}) not held by candidate.",
            )
        )

"""Hard-filter tests — unknown preferences must not reject."""

from __future__ import annotations

from app.agents.scout.hard_filters import apply_hard_filters
from app.agents.scout.profile_loader import profile_from_dict
from app.schemas.job_posting import NormalizedJob
from app.schemas.preferences import JobPreferences


def _candidate(**pref_overrides):
    prefs = {"home_location": "Cave Creek, Arizona", **pref_overrides}
    return profile_from_dict(
        {
            "identity": {"full_name": "Test"},
            "work_experience": [
                {
                    "company": "Acme",
                    "title": "Software Engineer",
                    "start_date": "2018-09",
                    "is_current": True,
                    "technologies": ["Java"],
                }
            ],
            "preferences": prefs,
        }
    )


def test_salary_below_minimum_hard_rejects() -> None:
    candidate = _candidate(minimum_base_salary=180000)
    job = NormalizedJob(
        company="Co",
        title="Engineer",
        salary_min=100000,
        salary_max=140000,
    )
    result = apply_hard_filters(candidate, job)
    assert result.passed is False
    assert any(r.code == "SALARY_BELOW_MINIMUM" for r in result.rejection_reasons)


def test_unknown_salary_does_not_hard_reject() -> None:
    candidate = _candidate(minimum_base_salary=180000)
    job = NormalizedJob(company="Co", title="Engineer")
    result = apply_hard_filters(candidate, job)
    assert result.passed is True
    assert any(w.code == "SALARY_UNKNOWN" for w in result.warnings)


def test_no_salary_preference_unknown_salary_does_not_reject() -> None:
    candidate = _candidate()
    job = NormalizedJob(company="Co", title="Engineer")
    result = apply_hard_filters(candidate, job)
    assert result.passed is True


def test_excluded_role_hard_filter() -> None:
    candidate = _candidate(excluded_roles=["Recruiter", "Sales"])
    job = NormalizedJob(company="Co", title="Technical Recruiter")
    result = apply_hard_filters(candidate, job)
    assert result.passed is False
    assert any(r.code == "EXCLUDED_ROLE" for r in result.rejection_reasons)


def test_unknown_preference_does_not_reject() -> None:
    candidate = _candidate()  # all prefs unknown
    job = NormalizedJob(
        company="Co",
        title="Whatever Role",
        remote_status="onsite",
        location="Elsewhere, NY",
        industry="gambling",
        salary_min=50000,
    )
    result = apply_hard_filters(candidate, job)
    assert result.passed is True


def test_remote_required_rejects_onsite() -> None:
    candidate = _candidate(remote_required=True, onsite_allowed=False)
    job = NormalizedJob(
        company="Co",
        title="Java Engineer",
        remote_status="onsite",
        location="Chicago, IL",
    )
    result = apply_hard_filters(candidate, job)
    assert result.passed is False
    assert any(r.code == "REMOTE_REQUIRED" for r in result.rejection_reasons)


def test_preferences_model_defaults_unknown() -> None:
    prefs = JobPreferences()
    assert prefs.minimum_base_salary is None
    assert prefs.remote_required is None
    assert prefs.target_roles is None

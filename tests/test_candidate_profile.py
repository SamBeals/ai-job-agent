"""Phase 2A candidate profile schema tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agents.scout.profile_loader import CandidateProfileError, load_candidate_profile, profile_from_dict
from app.schemas.candidate import CandidateProfile


FIXTURES = Path(__file__).resolve().parents[1] / "data"


def test_example_profile_validates() -> None:
    profile = load_candidate_profile(FIXTURES / "candidate_profile.example.json")
    assert profile.identity.full_name == "Alex Example"
    assert profile.preferences.minimum_base_salary is None


def test_local_profile_validates_if_present() -> None:
    path = FIXTURES / "candidate_profile.json"
    if not path.exists():
        pytest.skip("local candidate_profile.json not present")
    profile = load_candidate_profile(path)
    assert profile.identity.full_name
    # Private prefs may be configured locally; only assert schema validity / types.
    assert profile.preferences.minimum_base_salary is None or isinstance(
        profile.preferences.minimum_base_salary, int
    )
    assert profile.preferences.remote_required in {None, True, False}
    assert profile.work_experience


def test_invalid_profile_fails_clearly() -> None:
    with pytest.raises(CandidateProfileError):
        profile_from_dict({"not_a_profile": True})


def test_missing_profile_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(CandidateProfileError, match="not found"):
        load_candidate_profile(tmp_path / "missing.json")


def test_unknown_optional_fields_remain_unknown() -> None:
    profile = CandidateProfile.model_validate(
        {
            "identity": {"full_name": "Test"},
            "preferences": {},
        }
    )
    assert profile.preferences.target_roles is None
    assert profile.preferences.minimum_base_salary is None
    assert profile.skills.languages == []


def test_masters_in_progress_not_completed() -> None:
    raw = json.loads((FIXTURES / "candidate_profile.example.json").read_text())
    # inject in-progress masters like real profile
    raw["education"].append(
        {
            "institution": "Online U",
            "degree": "Master's",
            "field": "Computer Science",
            "status": "in_progress",
        }
    )
    profile = CandidateProfile.model_validate(raw)
    masters = [e for e in profile.education if "master" in e.degree.lower()]
    assert masters
    assert all(m.status != "completed" for m in masters)


def test_skill_listed_without_years() -> None:
    profile = load_candidate_profile(FIXTURES / "candidate_profile.example.json")
    terraform = next(
        (s for s in profile.skills.cloud_and_infra if s.name.lower() == "terraform"),
        None,
    )
    assert terraform is not None
    assert terraform.years_experience is None
    assert terraform.proficiency is None


def test_years_derived_from_dates_not_hardcoded() -> None:
    profile = load_candidate_profile(
        FIXTURES / "fixtures/profiles/test_remote_required.json"
    )
    years = profile.approximate_years_of_experience()
    assert years is not None
    assert years >= 7.0

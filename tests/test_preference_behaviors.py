"""Preference behavior tests using sanitized fixtures (not the private profile)."""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.scout.desirability import score_desirability
from app.agents.scout.hard_filters import apply_hard_filters
from app.agents.scout.llm.mock import MockLLMClient
from app.agents.scout.pipeline import ScoutPipeline
from app.agents.scout.profile_loader import load_candidate_profile
from app.agents.scout.scoring import ScoutThresholds
from app.schemas.evaluation import Recommendation
from app.schemas.job_posting import NormalizedJob


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "data" / "fixtures" / "profiles" / "test_office_backend_prefs.json"
SCOUT = ROOT / "data" / "fixtures" / "scout"


def _profile():
    return load_candidate_profile(PROFILE)


def _job(name: str) -> NormalizedJob:
    return NormalizedJob.model_validate(json.loads((SCOUT / name).read_text(encoding="utf-8")))


def _evaluate(job: NormalizedJob):
    pipeline = ScoutPipeline(llm_client=MockLLMClient(), thresholds=ScoutThresholds())
    return pipeline.evaluate(job, _profile()).evaluation


def test_salary_100_to_109_hard_rejects() -> None:
    job = NormalizedJob(
        company="Co",
        title="Software Engineer",
        salary_min=100000,
        salary_max=109000,
    )
    result = apply_hard_filters(_profile(), job)
    assert result.passed is False
    assert any(r.code == "SALARY_BELOW_MINIMUM" for r in result.rejection_reasons)


def test_salary_100_to_120_does_not_hard_reject() -> None:
    job = NormalizedJob(
        company="Co",
        title="Software Engineer",
        salary_min=100000,
        salary_max=120000,
    )
    result = apply_hard_filters(_profile(), job)
    assert result.passed is True


def test_salary_110k_exact_passes() -> None:
    job = NormalizedJob(
        company="Co",
        title="Software Engineer",
        salary_min=110000,
        salary_max=None,
    )
    result = apply_hard_filters(_profile(), job)
    assert result.passed is True


def test_missing_salary_unknown_not_reject() -> None:
    job = NormalizedJob(company="Co", title="Software Engineer")
    result = apply_hard_filters(_profile(), job)
    assert result.passed is True
    assert any(w.code == "SALARY_UNKNOWN" for w in result.warnings)
    evaluation = _evaluate(
        NormalizedJob(
            company="Co",
            title="Backend Software Engineer",
            remote_status="remote",
            required_skills=["Java", "Spring Boot", "REST APIs", "AWS", "SQL"],
            required_years_experience=5,
            education_requirements=["Bachelor's degree in Computer Science"],
        )
    )
    assert evaluation.recommendation != Recommendation.HARD_REJECT
    assert any("compensation" in u.lower() or "salary" in u.lower() for u in evaluation.uncertainties)


def test_hybrid_beats_identical_remote() -> None:
    hybrid = _job("pref_chandler_hybrid_backend.json")
    remote = NormalizedJob(
        **{
            **hybrid.model_dump(),
            "external_id": "same-role-remote",
            "location": "Remote - US",
            "remote_status": "remote",
        }
    )
    hybrid_score = score_desirability(_profile().preferences, hybrid).score
    remote_score = score_desirability(_profile().preferences, remote).score
    assert hybrid_score > remote_score


def test_remote_can_still_score_high() -> None:
    evaluation = _evaluate(_job("pref_remote_backend.json"))
    assert evaluation.desirability_score >= 75
    assert evaluation.recommendation in {
        Recommendation.STRONG_RECOMMEND,
        Recommendation.RECOMMEND,
        Recommendation.MAYBE,
    }


def test_chandler_location_advantage() -> None:
    chandler = _job("pref_chandler_hybrid_backend.json")
    tempe = NormalizedJob(
        **{
            **chandler.model_dump(),
            "external_id": "tempe-hybrid-backend",
            "location": "Tempe, Arizona",
        }
    )
    chandler_score = score_desirability(_profile().preferences, chandler).score
    tempe_score = score_desirability(_profile().preferences, tempe).score
    assert chandler_score >= tempe_score


def test_phoenix_metro_not_rejected_for_non_chandler() -> None:
    for city in (
        "Tempe, Arizona",
        "Mesa, Arizona",
        "Phoenix, Arizona",
        "Scottsdale, Arizona",
        "Gilbert, Arizona",
    ):
        job = NormalizedJob(
            company="Co",
            title="Backend Software Engineer",
            location=city,
            remote_status="hybrid",
            salary_min=140000,
            salary_max=160000,
            required_skills=["Java", "Spring Boot"],
        )
        hard = apply_hard_filters(_profile(), job)
        assert hard.passed is True, city
        desire = score_desirability(_profile().preferences, job)
        assert desire.score >= 75, city


def test_backend_beats_frontend_desirability() -> None:
    backend = _evaluate(_job("pref_chandler_hybrid_backend.json"))
    frontend = _evaluate(_job("pref_chandler_frontend.json"))
    assert backend.desirability_score > frontend.desirability_score


def test_fullstack_with_backend_remains_viable() -> None:
    job = NormalizedJob(
        company="Co",
        title="Full Stack Software Engineer",
        location="Chandler, Arizona",
        remote_status="hybrid",
        salary_min=140000,
        salary_max=160000,
        description="Build Java Spring Boot APIs and a modest React admin UI.",
        responsibilities=[
            "Develop backend services and REST APIs in Java/Spring Boot",
            "Maintain a small React admin console",
            "Deploy services on AWS",
        ],
        required_skills=["Java", "Spring Boot", "REST APIs", "React", "AWS"],
        required_years_experience=5,
        education_requirements=["Bachelor's degree in Computer Science"],
    )
    evaluation = _evaluate(job)
    assert evaluation.desirability_score >= 70
    assert evaluation.recommendation != Recommendation.HARD_REJECT


def test_development_heavy_beats_support_role() -> None:
    dev = _job("pref_chandler_hybrid_backend.json")
    support = NormalizedJob(
        company="Co",
        title="Production Support Engineer",
        location="Chandler, Arizona",
        remote_status="hybrid",
        salary_min=140000,
        salary_max=160000,
        description=(
            "Primary focus on production support, incident queues, and operational support. "
            "Occasional scripting."
        ),
        responsibilities=[
            "Handle production support tickets",
            "Operate help desk escalations",
            "Write incident reports",
        ],
        required_skills=["SQL", "monitoring"],
    )
    dev_score = score_desirability(_profile().preferences, dev).score
    support_score = score_desirability(_profile().preferences, support).score
    assert dev_score > support_score


def test_qualification_independent_of_desirability() -> None:
    backend = _evaluate(_job("pref_chandler_hybrid_backend.json"))
    frontend = _evaluate(_job("pref_chandler_frontend.json"))
    assert backend.qualification_score >= 70
    assert frontend.desirability_score < backend.desirability_score


def test_salary_hard_reject_overrides_excellent_fit() -> None:
    evaluation = _evaluate(_job("pref_chandler_backend_low_salary.json"))
    assert evaluation.recommendation == Recommendation.HARD_REJECT
    assert evaluation.qualification_score >= 70
    assert evaluation.desirability_score == 0


def test_unknown_seniority_does_not_penalize() -> None:
    with_seniority = _job("pref_remote_backend.json")
    without = NormalizedJob(
        **{**with_seniority.model_dump(), "seniority": None, "external_id": "no-seniority"}
    )
    a = score_desirability(_profile().preferences, with_seniority).score
    b = score_desirability(_profile().preferences, without).score
    assert abs(a - b) <= 3


def test_title_family_matching_not_overly_literal() -> None:
    job = NormalizedJob(
        company="Co",
        title="Senior Backend Engineer",
        location="Remote - US",
        remote_status="remote",
        salary_min=150000,
        salary_max=170000,
        required_skills=["Java", "Spring Boot", "REST APIs"],
    )
    desire = score_desirability(_profile().preferences, job)
    assert desire.score >= 75
    assert any("align" in s.lower() or "backend" in s.lower() for s in desire.strengths)


def test_software_development_engineer_title_matches() -> None:
    job = NormalizedJob(
        company="Co",
        title="Software Development Engineer",
        location="Phoenix, Arizona",
        remote_status="hybrid",
        salary_min=140000,
        salary_max=160000,
        description="Develop backend services and APIs.",
        responsibilities=["Design and implement software services"],
        required_skills=["Java", "AWS"],
    )
    desire = score_desirability(_profile().preferences, job)
    assert desire.score >= 75

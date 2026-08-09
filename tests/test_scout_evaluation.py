"""Evidence matching and qualification/desirability independence tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.scout.evidence_matcher import match_skills
from app.agents.scout.llm.mock import MockLLMClient
from app.agents.scout.llm.base import build_deterministic_context
from app.agents.scout.hard_filters import apply_hard_filters
from app.agents.scout.pipeline import ScoutPipeline
from app.agents.scout.profile_loader import load_candidate_profile
from app.agents.scout.scoring import ScoutThresholds, apply_recommendation_rules
from app.schemas.evidence import EvidenceStrength
from app.schemas.evaluation import Recommendation
from app.schemas.job_posting import NormalizedJob


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "data" / "fixtures" / "profiles" / "test_remote_required.json"
SCOUT = ROOT / "data" / "fixtures" / "scout"


def _load_job(name: str) -> NormalizedJob:
    return NormalizedJob.model_validate(
        json.loads((SCOUT / name).read_text(encoding="utf-8"))
    )


def test_professional_experience_outranks_listed_skill() -> None:
    candidate = load_candidate_profile(PROFILE)
    job = _load_job("fixture_a_strong_backend.json")
    report = match_skills(candidate, job)
    java = next(e for e in report.matching_skills if e.skill.lower() == "java")
    assert java.strength == EvidenceStrength.PROFESSIONAL_EXPERIENCE

    # Terraform is listed-only on full profile; on test profile may be listed
    terraform_job = NormalizedJob(
        company="Co",
        title="Eng",
        required_skills=["Terraform"],
    )
    # Add terraform as listed skill only via example profile style
    from app.agents.scout.profile_loader import profile_from_dict

    listed = profile_from_dict(
        {
            "identity": {"full_name": "T"},
            "skills": {"cloud_and_infra": ["Terraform"]},
            "work_experience": [
                {
                    "company": "A",
                    "title": "E",
                    "start_date": "2020-01",
                    "is_current": True,
                    "technologies": ["Java"],
                }
            ],
        }
    )
    tf_report = match_skills(listed, terraform_job)
    assert tf_report.matching_skills
    assert tf_report.matching_skills[0].strength == EvidenceStrength.LISTED_SKILL


def test_high_qualification_low_desirability_possible() -> None:
    candidate = load_candidate_profile(PROFILE)
    job = _load_job("fixture_c_onsite_undesirable.json")
    pipeline = ScoutPipeline(llm_client=MockLLMClient(), thresholds=ScoutThresholds())
    result = pipeline.evaluate(job, candidate)
    assert result.evaluation.qualification_score >= 70
    # Hard reject due to remote_required → desirability 0 / HARD_REJECT
    assert result.evaluation.recommendation == Recommendation.HARD_REJECT
    assert result.evaluation.desirability_score <= 30


def test_low_qualification_high_desirability_possible() -> None:
    candidate = load_candidate_profile(PROFILE)
    # ML job is remote (desirable under remote_required) but poor qualification
    job = _load_job("fixture_b_ml_research.json")
    pipeline = ScoutPipeline(llm_client=MockLLMClient(), thresholds=ScoutThresholds())
    result = pipeline.evaluate(job, candidate)
    assert result.evaluation.qualification_score < 45
    assert result.evaluation.desirability_score >= 70


def test_missing_preferred_skill_smaller_than_missing_required() -> None:
    candidate = load_candidate_profile(PROFILE)
    strong = _load_job("fixture_a_strong_backend.json")
    preferred_gap = _load_job("fixture_f_preferred_gap.json")
    missing_required = NormalizedJob(
        company="Co",
        title="Senior Backend Software Engineer",
        remote_status="remote",
        required_skills=["Java", "Spring Boot", "Kafka"],
        preferred_skills=[],
        required_years_experience=5,
        education_requirements=["Bachelor's degree in Computer Science"],
    )
    client = MockLLMClient()
    pipeline = ScoutPipeline(llm_client=client, thresholds=ScoutThresholds())
    gap = pipeline.evaluate(preferred_gap, candidate).evaluation.qualification_score
    miss = pipeline.evaluate(missing_required, candidate).evaluation.qualification_score
    base = pipeline.evaluate(strong, candidate).evaluation.qualification_score
    assert gap >= base - 15
    assert miss < gap


def test_keyword_trap_java_not_javascript_expert() -> None:
    candidate = load_candidate_profile(PROFILE)
    job = _load_job("fixture_e_keyword_trap.json")
    report = match_skills(candidate, job)
    # JavaScript may be listed skill on real profile; Node/React should be missing or weak
    assert "Node.js" in report.missing_required_skills or any(
        "node" in m.skill.lower() for m in report.partial_matches
    )
    pipeline = ScoutPipeline(llm_client=MockLLMClient(), thresholds=ScoutThresholds())
    result = pipeline.evaluate(job, candidate)
    # Should not look like a strong backend Java match
    assert result.evaluation.qualification_score < 75


def test_missing_info_does_not_arbitrarily_penalize() -> None:
    candidate = load_candidate_profile(PROFILE)
    job = _load_job("fixture_d_missing_info.json")
    hard = apply_hard_filters(candidate, job)
    assert hard.passed is True
    pipeline = ScoutPipeline(llm_client=MockLLMClient(), thresholds=ScoutThresholds())
    result = pipeline.evaluate(job, candidate)
    assert result.evaluation.qualification_score >= 60
    assert any("unknown" in u.lower() for u in result.evaluation.uncertainties)
    assert result.evaluation.confidence.value in {"LOW", "MEDIUM"}


def test_strong_backend_high_qualification() -> None:
    candidate = load_candidate_profile(PROFILE)
    job = _load_job("fixture_a_strong_backend.json")
    pipeline = ScoutPipeline(llm_client=MockLLMClient(), thresholds=ScoutThresholds())
    result = pipeline.evaluate(job, candidate)
    assert result.evaluation.qualification_score >= 75
    assert result.evaluation.recommendation in {
        Recommendation.STRONG_RECOMMEND,
        Recommendation.RECOMMEND,
        Recommendation.MAYBE,
    }

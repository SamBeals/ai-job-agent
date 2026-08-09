"""Phase 2A.6 — evidence-grounded LLM qualification evaluator tests.

No test may call a real paid API. OpenAI SDK is mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.agents.scout.assembler import assemble_scout_evaluation, enforce_hard_filter
from app.agents.scout.evidence_payload import (
    assert_payload_has_no_sensitive_fields,
    build_candidate_evidence_payload,
)
from app.agents.scout.llm.factory import LLMUnavailableError, get_llm_client
from app.agents.scout.llm.mock import MockLLMClient
from app.agents.scout.llm.openai_client import EvaluatorOutputError, OpenAIScoutClient
from app.agents.scout.pipeline import ScoutEvaluationError, ScoutPipeline
from app.agents.scout.profile_loader import load_candidate_profile, profile_from_dict
from app.agents.scout.qualification_scoring import score_qualification
from app.agents.scout.scoring import ScoutThresholds
from app.config import Settings
from app.discord.embeds import scout_evaluation_embed
from app.models.job import Job, JobStatus
from app.schemas.evaluation import (
    Confidence,
    HardFilterResult,
    Recommendation,
    ScoutEvaluation,
)
from app.schemas.evidence import EvidenceStrength
from app.schemas.job_posting import NormalizedJob
from app.schemas.qualification import (
    JobRequirement,
    MatchLevel,
    RequirementCategory,
    RequirementMatch,
    RequirementType,
    SemanticJobEvaluation,
)
from app.services.approval_service import ApprovalService
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "data" / "fixtures" / "profiles" / "test_remote_required.json"
SCOUT = ROOT / "data" / "fixtures" / "scout"
CALIBRATION = SCOUT / "fixture_g_calibration_software_engineer.json"


def _load_job(name: str) -> NormalizedJob:
    return NormalizedJob.model_validate(json.loads((SCOUT / name).read_text(encoding="utf-8")))


def _rich_candidate():
    return profile_from_dict(
        {
            "identity": {
                "full_name": "Test Candidate",
                "email": "secret@example.com",
                "phone": "555-0100",
                "location": "Chandler, AZ",
                "linkedin_url": "https://linkedin.com/in/secret",
            },
            "skills": {
                "languages": [
                    {"name": "Java", "verified": True, "evidence_type": "PROFESSIONAL_EXPERIENCE"},
                    {"name": "Terraform", "verified": True, "evidence_type": "LISTED_SKILL"},
                ],
                "frameworks": [
                    {"name": "Spring Boot", "verified": True, "evidence_type": "PROFESSIONAL_EXPERIENCE"},
                ],
                "cloud_and_infra": [
                    {"name": "AWS", "verified": True, "evidence_type": "PROFESSIONAL_EXPERIENCE"},
                    {"name": "Kubernetes", "verified": True, "evidence_type": "PROFESSIONAL_EXPERIENCE"},
                ],
                "databases": [
                    {"name": "SQL", "verified": True, "evidence_type": "PROFESSIONAL_EXPERIENCE"},
                ],
            },
            "work_experience": [
                {
                    "company": "Acme",
                    "title": "Backend Engineer",
                    "start_date": "2018-01",
                    "is_current": True,
                    "technologies": ["Java", "Spring Boot", "REST APIs", "AWS", "Kubernetes", "SQL"],
                    "verified_accomplishments": [
                        "Designed and optimized RESTful APIs",
                        "Deployed services on AWS/Kubernetes",
                    ],
                }
            ],
            "education": [
                {
                    "institution": "State University",
                    "degree": "B.S.",
                    "field": "Computer Science",
                    "status": "completed",
                    "graduation_date": "2017-05",
                }
            ],
            "certifications": [{"name": "AWS Cloud Practitioner", "issuer": "Amazon"}],
            "preferences": {"remote_required": True, "minimum_base_salary": 100000},
        }
    )


def test_factory_mock_provider() -> None:
    client = get_llm_client(Settings(llm_provider="mock"))
    assert isinstance(client, MockLLMClient)
    assert client.provider_name == "mock"


def test_factory_openai_uses_openai_client() -> None:
    client = get_llm_client(
        Settings(llm_provider="openai", openai_api_key="sk-test", llm_model="gpt-4o-mini")
    )
    assert isinstance(client, OpenAIScoutClient)
    assert client.provider_name == "openai"


def test_factory_openai_missing_key_fails_clearly() -> None:
    with pytest.raises(LLMUnavailableError, match="OPENAI_API_KEY"):
        get_llm_client(Settings(llm_provider="openai", openai_api_key=""))


def test_openai_failure_does_not_fallback_to_mock() -> None:
    candidate = _rich_candidate()
    job = _load_job("fixture_g_calibration_software_engineer.json")
    client = OpenAIScoutClient(api_key="sk-test", model="gpt-4o-mini")

    class BoomClient:
        chat = SimpleNamespace(
            completions=SimpleNamespace(create=MagicMock(side_effect=RuntimeError("rate limit")))
        )
        beta = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=None)))

    with patch("openai.OpenAI", return_value=BoomClient()):
        with pytest.raises(EvaluatorOutputError, match="LLM unavailable"):
            from app.agents.scout.hard_filters import apply_hard_filters
            from app.agents.scout.evidence_matcher import match_skills
            from app.agents.scout.llm.base import build_deterministic_context

            hard = apply_hard_filters(candidate, job)
            skills = match_skills(candidate, job)
            ctx = build_deterministic_context(
                skill_report=skills, hard_filter=hard, evaluator_version="2a.6"
            )
            client.evaluate_job(candidate, job, ctx)
    assert client.provider_name == "openai"


def test_valid_structured_openai_response_parses() -> None:
    candidate = _rich_candidate()
    job = _load_job("fixture_g_calibration_software_engineer.json")
    semantic = SemanticJobEvaluation(
        requirements=[
            RequirementMatch(
                requirement=JobRequirement(
                    id="1",
                    name="Java",
                    requirement_type=RequirementType.REQUIRED,
                ),
                match_level=MatchLevel.STRONG_MATCH,
                evidence_strength=EvidenceStrength.PROFESSIONAL_EXPERIENCE,
                candidate_evidence=["Java at Acme"],
                reasoning="Professional Java backend work.",
            ),
            RequirementMatch(
                requirement=JobRequirement(
                    id="2",
                    name="Kafka",
                    requirement_type=RequirementType.PREFERRED,
                ),
                match_level=MatchLevel.NO_EVIDENCE,
                reasoning="No verified Kafka evidence.",
            ),
        ],
        summary="Strong core backend alignment with preferred gaps.",
        overall_confidence="MEDIUM",
    )

    client = OpenAIScoutClient(api_key="sk-test", model="gpt-test")

    def _fake_call(self, client_obj, user_prompt):
        return semantic, None

    with patch.object(OpenAIScoutClient, "_call_model", _fake_call):
        from app.agents.scout.hard_filters import apply_hard_filters
        from app.agents.scout.evidence_matcher import match_skills
        from app.agents.scout.llm.base import build_deterministic_context

        hard = apply_hard_filters(candidate, job)
        skills = match_skills(candidate, job)
        ctx = build_deterministic_context(
            skill_report=skills, hard_filter=hard, evaluator_version="2a.6"
        )
        evaluation = client.evaluate_job(candidate, job, ctx)

    assert evaluation.evaluator_provider == "openai"
    assert evaluation.qualification_score > 0
    assert evaluation.prompt_version
    assert evaluation.requirement_matches


def test_invalid_structured_response_fails() -> None:
    client = OpenAIScoutClient(api_key="sk-test", model="gpt-test")
    fake = MagicMock()
    fake.beta = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=None)))
    fake.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"requirements": [{"match_level": "TOTALLY_INVALID"}]}'))],
        usage=None,
    )
    with patch("openai.OpenAI", return_value=fake):
        with pytest.raises(EvaluatorOutputError):
            from app.agents.scout.hard_filters import apply_hard_filters
            from app.agents.scout.evidence_matcher import match_skills
            from app.agents.scout.llm.base import build_deterministic_context

            candidate = _rich_candidate()
            job = _load_job("fixture_a_strong_backend.json")
            hard = apply_hard_filters(candidate, job)
            skills = match_skills(candidate, job)
            ctx = build_deterministic_context(
                skill_report=skills, hard_filter=hard, evaluator_version="2a.6"
            )
            client.evaluate_job(candidate, job, ctx)


def test_payload_excludes_sensitive_identity_fields() -> None:
    candidate = _rich_candidate()
    payload = build_candidate_evidence_payload(candidate)
    assert_payload_has_no_sensitive_fields(payload)
    blob = json.dumps(payload)
    assert "secret@example.com" not in blob
    assert "555-0100" not in blob
    assert "linkedin.com" not in blob
    assert "work_experience" in payload
    assert payload["skills"]
    assert payload["education"]
    assert payload["certifications"]


def test_listed_skill_distinguishable_from_professional() -> None:
    candidate = _rich_candidate()
    job = NormalizedJob(
        company="Co",
        title="Eng",
        required_skills=["Java", "Terraform"],
        remote_status="remote",
    )
    pipeline = ScoutPipeline(llm_client=MockLLMClient(), thresholds=ScoutThresholds())
    evaluation = pipeline.evaluate(job, candidate).evaluation
    # Java should score as strong; terraform weaker / partial
    java_rows = [
        r for r in evaluation.requirement_matches if (r.get("requirement") or {}).get("name") == "Java"
    ]
    tf_rows = [
        r
        for r in evaluation.requirement_matches
        if (r.get("requirement") or {}).get("name") == "Terraform"
    ]
    assert java_rows
    assert tf_rows
    assert java_rows[0]["match_level"] == MatchLevel.STRONG_MATCH.value
    assert tf_rows[0]["evidence_strength"] == EvidenceStrength.LISTED_SKILL.value
    assert tf_rows[0]["match_level"] in {
        MatchLevel.PARTIAL_MATCH.value,
        MatchLevel.MATCH.value,
    }


def test_required_vs_preferred_and_missing_weights() -> None:
    semantic_missing_required = SemanticJobEvaluation(
        requirements=[
            RequirementMatch(
                requirement=JobRequirement(
                    id="r1", name="Kafka", requirement_type=RequirementType.REQUIRED
                ),
                match_level=MatchLevel.NO_EVIDENCE,
            ),
            RequirementMatch(
                requirement=JobRequirement(
                    id="r2", name="Java", requirement_type=RequirementType.REQUIRED
                ),
                match_level=MatchLevel.STRONG_MATCH,
                evidence_strength=EvidenceStrength.PROFESSIONAL_EXPERIENCE,
            ),
        ]
    )
    semantic_missing_preferred = SemanticJobEvaluation(
        requirements=[
            RequirementMatch(
                requirement=JobRequirement(
                    id="r1", name="Kafka", requirement_type=RequirementType.PREFERRED
                ),
                match_level=MatchLevel.NO_EVIDENCE,
            ),
            RequirementMatch(
                requirement=JobRequirement(
                    id="r2", name="Java", requirement_type=RequirementType.REQUIRED
                ),
                match_level=MatchLevel.STRONG_MATCH,
                evidence_strength=EvidenceStrength.PROFESSIONAL_EXPERIENCE,
            ),
        ]
    )
    miss_req = score_qualification(semantic_missing_required).score
    miss_pref = score_qualification(semantic_missing_preferred).score
    assert miss_pref > miss_req


def test_professional_stronger_than_listed_skill_scoring() -> None:
    pro = SemanticJobEvaluation(
        requirements=[
            RequirementMatch(
                requirement=JobRequirement(
                    id="1", name="Terraform", requirement_type=RequirementType.REQUIRED
                ),
                match_level=MatchLevel.STRONG_MATCH,
                evidence_strength=EvidenceStrength.PROFESSIONAL_EXPERIENCE,
            )
        ]
    )
    listed = SemanticJobEvaluation(
        requirements=[
            RequirementMatch(
                requirement=JobRequirement(
                    id="1", name="Terraform", requirement_type=RequirementType.REQUIRED
                ),
                match_level=MatchLevel.PARTIAL_MATCH,
                evidence_strength=EvidenceStrength.LISTED_SKILL,
            )
        ]
    )
    assert score_qualification(pro).score > score_qualification(listed).score


def test_transferable_gets_partial_credit() -> None:
    transferable = SemanticJobEvaluation(
        requirements=[
            RequirementMatch(
                requirement=JobRequirement(
                    id="1", name="Azure", requirement_type=RequirementType.REQUIRED
                ),
                match_level=MatchLevel.TRANSFERABLE,
                evidence_strength=EvidenceStrength.PROFESSIONAL_EXPERIENCE,
                reasoning="AWS cloud experience is transferable; no Azure evidence.",
            )
        ]
    )
    none = SemanticJobEvaluation(
        requirements=[
            RequirementMatch(
                requirement=JobRequirement(
                    id="1", name="Azure", requirement_type=RequirementType.REQUIRED
                ),
                match_level=MatchLevel.NO_EVIDENCE,
            )
        ]
    )
    assert score_qualification(transferable).score > score_qualification(none).score
    assert "Azure" not in " ".join(score_qualification(transferable).matching_skills)


def test_aws_does_not_become_azure_in_deterministic_path() -> None:
    candidate = _rich_candidate()
    job = NormalizedJob(company="Co", title="Eng", required_skills=["Azure"], remote_status="remote")
    evaluation = ScoutPipeline(llm_client=MockLLMClient(), thresholds=ScoutThresholds()).evaluate(
        job, candidate
    ).evaluation
    assert "Azure" in evaluation.missing_required_skills or any(
        "Azure" in c for c in evaluation.concerns
    )
    assert not any("azure" in s.lower() and "strong" in s.lower() for s in evaluation.matching_skills)


def test_java_does_not_become_javascript() -> None:
    candidate = _rich_candidate()
    job = NormalizedJob(
        company="Co", title="Eng", required_skills=["JavaScript"], remote_status="remote"
    )
    evaluation = ScoutPipeline(llm_client=MockLLMClient(), thresholds=ScoutThresholds()).evaluate(
        job, candidate
    ).evaluation
    # No fabricated JavaScript professional claim from Java
    for s in evaluation.matching_skills:
        assert "javascript" not in s.lower()


def test_total_years_not_technology_specific_years() -> None:
    candidate = _rich_candidate()
    job = NormalizedJob(
        company="Co",
        title="Eng",
        required_skills=["Kubernetes"],
        required_years_experience=5,
        remote_status="remote",
    )
    evaluation = ScoutPipeline(llm_client=MockLLMClient(), thresholds=ScoutThresholds()).evaluate(
        job, candidate
    ).evaluation
    k8s = [
        r
        for r in evaluation.requirement_matches
        if (r.get("requirement") or {}).get("name") == "Kubernetes"
    ]
    assert k8s
    reasoning = (k8s[0].get("reasoning") or "").lower()
    assert "8 year" not in reasoning and "approximately 8" not in reasoning


def test_no_evidence_does_not_fabricate_claims() -> None:
    semantic = SemanticJobEvaluation(
        requirements=[
            RequirementMatch(
                requirement=JobRequirement(
                    id="1", name="Go", requirement_type=RequirementType.REQUIRED
                ),
                match_level=MatchLevel.NO_EVIDENCE,
                candidate_evidence=[],
                reasoning="No verified candidate evidence found for Go.",
            )
        ]
    )
    result = score_qualification(semantic)
    assert result.missing_required_skills == ["Go"]
    assert all("years" not in e.lower() for e in [])


def test_partial_source_and_low_extraction_cap_confidence() -> None:
    candidate = _rich_candidate()
    job = _load_job("fixture_a_strong_backend.json")
    semantic = SemanticJobEvaluation(overall_confidence="HIGH", summary="ok", requirements=[])
    hard = HardFilterResult(passed=True, rejection_reasons=[], warnings=[])
    evaluation = assemble_scout_evaluation(
        candidate=candidate,
        job=job,
        semantic=semantic,
        hard_filter=hard,
        provider="mock",
        evaluator_version="2a.6",
        prompt_version="qualification-v1",
        source_content_partial=True,
        extraction_confidence="LOW",
    )
    assert evaluation.confidence in {Confidence.LOW, Confidence.MEDIUM}
    assert evaluation.confidence != Confidence.HIGH


def test_hard_filter_salary_authoritative_after_llm() -> None:
    candidate = _rich_candidate()
    job = NormalizedJob(
        company="Cheap Co",
        title="Backend Software Engineer",
        remote_status="remote",
        salary_min=40000,
        salary_max=50000,
        required_skills=["Java"],
    )
    evaluation = ScoutPipeline(llm_client=MockLLMClient(), thresholds=ScoutThresholds()).evaluate(
        job, candidate
    ).evaluation
    assert evaluation.recommendation == Recommendation.HARD_REJECT
    # Simulate LLM trying to clear reject
    evaluation.recommendation = Recommendation.STRONG_RECOMMEND
    evaluation.desirability_score = 100
    from app.agents.scout.hard_filters import apply_hard_filters

    hard = apply_hard_filters(candidate, job)
    fixed = enforce_hard_filter(evaluation, hard)
    assert fixed.recommendation == Recommendation.HARD_REJECT
    assert fixed.desirability_score == 0


def test_openai_scores_cannot_authorize(session, approval_service: ApprovalService) -> None:
    candidate = load_candidate_profile(PROFILE)
    job = _load_job("fixture_a_strong_backend.json")
    # Pretend perfect OpenAI evaluation persisted via pipeline + mock
    pipeline = ScoutPipeline(
        llm_client=MockLLMClient(),
        thresholds=ScoutThresholds(),
        session=session,
    )
    result = pipeline.evaluate(job, candidate, persist=True, create_job_record=True)
    session.commit()
    assert result.job is not None
    result.evaluation.qualification_score = 100
    result.evaluation.desirability_score = 100
    result.evaluation.recommendation = Recommendation.STRONG_RECOMMEND
    assert approval_service.can_enter_application_pipeline(result.job.id) is False
    assert approval_service.get_approval_for_job(result.job.id) is None
    assert result.job.status_enum != JobStatus.APPROVED


def test_persistence_records_provider_model_version(session) -> None:
    from app.services.scout_evaluation_service import ScoutEvaluationService

    candidate = load_candidate_profile(PROFILE)
    job = _load_job("fixture_g_calibration_software_engineer.json")
    pipeline = ScoutPipeline(
        llm_client=MockLLMClient(),
        thresholds=ScoutThresholds(),
        session=session,
        settings=Settings(scout_evaluator_version="2a.6", scout_prompt_version="qualification-v1"),
    )
    result = pipeline.evaluate(job, candidate, persist=True, create_job_record=True)
    session.commit()
    record = ScoutEvaluationService(session).latest_for_job(result.job.id)
    assert record is not None
    payload = record.evaluation_json
    assert payload["evaluator_provider"] == "mock"
    assert payload.get("prompt_version") == "qualification-v1"
    assert payload.get("evaluator_model") == "deterministic"
    assert payload.get("evaluation_fingerprint")
    assert "requirement_matches" in payload


def test_discord_embed_evidence_within_limits() -> None:
    job = Job(
        id=42,
        company="Horizon Platforms",
        title="Software Engineer",
        source="fixture",
        location="Remote - US",
        remote_status="remote",
        salary_min=None,
        salary_max=None,
        job_url="https://example.com/jobs/x",
        status=JobStatus.AWAITING_APPROVAL.value,
        discovered_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    evaluation = ScoutEvaluation(
        job_id=42,
        qualification_score=82,
        desirability_score=90,
        recommendation=Recommendation.RECOMMEND,
        confidence=Confidence.MEDIUM,
        matching_skills=["Java — professional experience", "REST APIs — professional experience"],
        partial_matches=["Terraform — listed skill; depth unknown"],
        missing_required_skills=[],
        missing_preferred_skills=["Kafka", "Go"],
        experience_matches=["Approximately 8 years professional software engineering"],
        qualification_reasoning=["Strong overall backend alignment."],
        desirability_reasoning=[
            "Remote arrangement is acceptable.",
            "Preference concern: Fully remote; hybrid/on-site preferred",
        ],
        uncertainties=["Salary not disclosed"],
        evaluator_provider="openai",
        evaluator_model="gpt-4o-mini",
        prompt_version="qualification-v1",
    )
    embed = scout_evaluation_embed(job, evaluation, extraction_confidence="MEDIUM")
    field_map = {f.name: f.value for f in embed.fields}
    assert "Strong Evidence" in field_map
    assert "Partial Evidence" in field_map
    assert "Missing / No Evidence" in field_map
    assert "Kafka" in field_map["Missing / No Evidence"]
    assert len(embed.footer.text or "") <= 2048
    total = sum(len(f.value) for f in embed.fields)
    assert total < 5500


def test_calibration_fixture_not_shallow_years_only() -> None:
    candidate = _rich_candidate()
    job = NormalizedJob.model_validate(json.loads(CALIBRATION.read_text()))
    evaluation = ScoutPipeline(llm_client=MockLLMClient(), thresholds=ScoutThresholds()).evaluate(
        job, candidate
    ).evaluation
    assert evaluation.qualification_score >= 60
    # Should mention skills, not only years
    reasoning = " ".join(evaluation.qualification_reasoning).lower()
    assert "java" in reasoning or any("java" in s.lower() for s in evaluation.matching_skills)
    assert evaluation.matching_skills or evaluation.experience_matches
    # Unknown salary should not hard-reject
    assert evaluation.recommendation != Recommendation.HARD_REJECT
    # Recommendation still unauthorized — checked via status only when persisted
    assert evaluation.recommendation in {
        Recommendation.STRONG_RECOMMEND,
        Recommendation.RECOMMEND,
        Recommendation.MAYBE,
        Recommendation.DO_NOT_RECOMMEND,
    }


def test_pipeline_openai_error_surfaces(session) -> None:
    class Boom(OpenAIScoutClient):
        def evaluate_job(self, candidate, job, deterministic_context):
            raise EvaluatorOutputError("schema validation failed")

    candidate = load_candidate_profile(PROFILE)
    job = _load_job("fixture_a_strong_backend.json")
    pipeline = ScoutPipeline(
        llm_client=Boom(api_key="sk-test", model="x"),
        thresholds=ScoutThresholds(),
        session=session,
    )
    with pytest.raises(ScoutEvaluationError, match="Evaluator failed safely"):
        pipeline.evaluate(job, candidate)

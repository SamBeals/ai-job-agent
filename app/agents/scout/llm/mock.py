"""Deterministic / mock Scout evaluator — evidence-grounded, no paid API."""

from __future__ import annotations

from app.agents.scout.assembler import assemble_scout_evaluation
from app.agents.scout.deterministic_semantic import build_deterministic_semantic_evaluation
from app.agents.scout.llm.base import DeterministicContext
from app.agents.scout.prompts.qualification import PROMPT_VERSION
from app.schemas.candidate import CandidateProfile
from app.schemas.evaluation import HardFilterResult, ScoutEvaluation
from app.schemas.evidence import SkillMatchReport
from app.schemas.job_posting import NormalizedJob


class MockLLMClient:
    """Deterministic evidence-based evaluator. Never calls a paid API. Never authorizes."""

    provider_name = "mock"

    def __init__(self, *, evaluator_version: str = "2a.6", prompt_version: str = PROMPT_VERSION) -> None:
        self.evaluator_version = evaluator_version
        self.prompt_version = prompt_version

    def evaluate_job(
        self,
        candidate: CandidateProfile,
        job: NormalizedJob,
        deterministic_context: DeterministicContext,
    ) -> ScoutEvaluation:
        skill_report = SkillMatchReport.model_validate(
            deterministic_context.get("skill_report") or {}
        )
        hard_filter = HardFilterResult.model_validate(
            deterministic_context.get("hard_filter") or {}
        )
        version = str(
            deterministic_context.get("evaluator_version") or self.evaluator_version
        )
        partial = bool(deterministic_context.get("source_content_partial"))
        extraction_confidence = deterministic_context.get("extraction_confidence")

        semantic = build_deterministic_semantic_evaluation(candidate, job, skill_report)
        return assemble_scout_evaluation(
            candidate=candidate,
            job=job,
            semantic=semantic,
            hard_filter=hard_filter,
            provider=self.provider_name,
            evaluator_version=version,
            prompt_version=self.prompt_version,
            model="deterministic",
            source_content_partial=partial,
            extraction_confidence=str(extraction_confidence) if extraction_confidence else None,
        )

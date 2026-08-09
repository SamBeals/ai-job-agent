"""LLM client abstraction for Scout evaluation."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.schemas.candidate import CandidateProfile
from app.schemas.evaluation import HardFilterResult, ScoutEvaluation
from app.schemas.evidence import SkillMatchReport
from app.schemas.job_posting import NormalizedJob


class DeterministicContext(dict):
    """Structured context passed into evaluators (not free-form application state)."""


@runtime_checkable
class LLMClient(Protocol):
    """Provider-agnostic Scout evaluator interface."""

    provider_name: str

    def evaluate_job(
        self,
        candidate: CandidateProfile,
        job: NormalizedJob,
        deterministic_context: DeterministicContext,
    ) -> ScoutEvaluation:
        """Return a validated ScoutEvaluation. Must not invent authorization."""
        ...


def build_deterministic_context(
    *,
    skill_report: SkillMatchReport,
    hard_filter: HardFilterResult,
    evaluator_version: str,
) -> DeterministicContext:
    return DeterministicContext(
        skill_report=skill_report.model_dump(),
        hard_filter=hard_filter.model_dump(),
        evaluator_version=evaluator_version,
    )

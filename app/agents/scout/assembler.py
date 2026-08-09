"""Assemble final ScoutEvaluation from semantic qualification + deterministic desirability."""

from __future__ import annotations

from datetime import datetime, timezone

from app.agents.scout.desirability import score_desirability
from app.agents.scout.fingerprint import compute_evaluation_fingerprint
from app.agents.scout.qualification_scoring import score_qualification
from app.schemas.candidate import CandidateProfile
from app.schemas.evaluation import (
    Confidence,
    HardFilterResult,
    Recommendation,
    ScoutEvaluation,
)
from app.schemas.job_posting import NormalizedJob
from app.schemas.qualification import (
    FocusLevel,
    JobCharacteristics,
    QualificationWeights,
    SemanticJobEvaluation,
    TokenUsage,
)


def assemble_scout_evaluation(
    *,
    candidate: CandidateProfile,
    job: NormalizedJob,
    semantic: SemanticJobEvaluation,
    hard_filter: HardFilterResult,
    provider: str,
    evaluator_version: str,
    prompt_version: str,
    model: str | None = None,
    token_usage: TokenUsage | None = None,
    source_content_partial: bool = False,
    extraction_confidence: str | None = None,
    weights: QualificationWeights | None = None,
) -> ScoutEvaluation:
    """Build ScoutEvaluation. Hard filters and desirability stay deterministic."""
    qual = score_qualification(semantic, weights=weights)
    fingerprint = compute_evaluation_fingerprint(
        job=job,
        candidate=candidate,
        prompt_version=prompt_version,
        model=model,
        provider=provider,
    )

    if not hard_filter.passed:
        dealbreakers = [r.message for r in hard_filter.rejection_reasons]
        return ScoutEvaluation(
            qualification_score=qual.score,
            desirability_score=0,
            recommendation=Recommendation.HARD_REJECT,
            confidence=_cap_confidence(
                _map_confidence(semantic.overall_confidence),
                source_content_partial=source_content_partial,
                extraction_confidence=extraction_confidence,
            ),
            matching_skills=qual.matching_skills,
            partial_matches=qual.partial_matches,
            missing_required_skills=qual.missing_required_skills,
            missing_preferred_skills=qual.missing_preferred_skills,
            experience_matches=qual.experience_matches,
            concerns=list(dealbreakers) + qual.concerns,
            dealbreakers=dealbreakers,
            qualification_reasoning=qual.reasoning,
            desirability_reasoning=[
                "Hard-filtered before preference scoring.",
                *dealbreakers,
            ],
            uncertainties=[w.message for w in hard_filter.warnings] + list(semantic.uncertainties),
            hard_filter=hard_filter,
            evaluated_at=datetime.now(timezone.utc),
            evaluator_version=evaluator_version,
            evaluator_provider=provider,
            prompt_version=prompt_version,
            evaluator_model=model,
            requirement_matches=[m.model_dump(mode="json") for m in qual.matches],
            job_characteristics=semantic.job_characteristics.model_dump(mode="json"),
            token_usage=token_usage.model_dump(mode="json") if token_usage else None,
            source_content_partial=source_content_partial,
            evaluation_fingerprint=fingerprint,
        )

    # Optional: apply semantic job characteristics into a shallow job copy for desirability
    desire_job = _apply_characteristics_hints(job, semantic.job_characteristics)
    desire = score_desirability(candidate.preferences, desire_job)
    desire_reason = list(desire.strengths)
    if desire.concerns:
        desire_reason.extend(f"Preference concern: {c}" for c in desire.concerns)

    uncertainties: list[str] = []
    for item in [w.message for w in hard_filter.warnings] + desire.unknowns + list(semantic.uncertainties):
        if item not in uncertainties:
            uncertainties.append(item)

    confidence = _cap_confidence(
        _map_confidence(semantic.overall_confidence),
        source_content_partial=source_content_partial,
        extraction_confidence=extraction_confidence,
    )

    return ScoutEvaluation(
        qualification_score=qual.score,
        desirability_score=desire.score,
        recommendation=Recommendation.MAYBE,  # pipeline applies thresholds
        confidence=confidence,
        matching_skills=qual.matching_skills,
        partial_matches=qual.partial_matches,
        missing_required_skills=qual.missing_required_skills,
        missing_preferred_skills=qual.missing_preferred_skills,
        experience_matches=qual.experience_matches,
        concerns=qual.concerns,
        dealbreakers=[],
        qualification_reasoning=qual.reasoning,
        desirability_reasoning=desire_reason,
        uncertainties=uncertainties,
        hard_filter=hard_filter,
        evaluated_at=datetime.now(timezone.utc),
        evaluator_version=evaluator_version,
        evaluator_provider=provider,
        prompt_version=prompt_version,
        evaluator_model=model,
        requirement_matches=[m.model_dump(mode="json") for m in qual.matches],
        job_characteristics=semantic.job_characteristics.model_dump(mode="json"),
        token_usage=token_usage.model_dump(mode="json") if token_usage else None,
        source_content_partial=source_content_partial,
        evaluation_fingerprint=fingerprint,
    )


def enforce_hard_filter(evaluation: ScoutEvaluation, hard_filter: HardFilterResult) -> ScoutEvaluation:
    """Re-apply hard filter after LLM assembly — LLM cannot clear dealbreakers."""
    if hard_filter.passed:
        evaluation.hard_filter = hard_filter
        return evaluation
    dealbreakers = [r.message for r in hard_filter.rejection_reasons]
    evaluation.recommendation = Recommendation.HARD_REJECT
    evaluation.desirability_score = 0
    evaluation.dealbreakers = dealbreakers
    evaluation.concerns = list(dict.fromkeys([*dealbreakers, *evaluation.concerns]))
    evaluation.desirability_reasoning = [
        "Hard-filtered before preference scoring.",
        *dealbreakers,
    ]
    evaluation.hard_filter = hard_filter
    return evaluation


def _map_confidence(value: str) -> Confidence:
    v = (value or "MEDIUM").upper()
    if v == "HIGH":
        return Confidence.HIGH
    if v == "LOW":
        return Confidence.LOW
    return Confidence.MEDIUM


def _cap_confidence(
    confidence: Confidence,
    *,
    source_content_partial: bool,
    extraction_confidence: str | None,
) -> Confidence:
    """Partial/low extraction content must not claim HIGH Scout confidence."""
    order = [Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH]
    max_allowed = Confidence.HIGH
    if source_content_partial:
        max_allowed = Confidence.MEDIUM
    if extraction_confidence and extraction_confidence.upper() == "LOW":
        max_allowed = Confidence.MEDIUM
    if order.index(confidence) > order.index(max_allowed):
        return max_allowed
    return confidence


def _apply_characteristics_hints(job: NormalizedJob, chars: JobCharacteristics) -> NormalizedJob:
    """Annotate description with semantic focus hints for preference scoring.

    Does not invent salary/location. Only appends structured hints the
    existing desirability keyword heuristics can read.
    """
    hints: list[str] = []
    if chars.backend_focus == FocusLevel.HIGH:
        hints.append("backend software development")
    if chars.frontend_focus == FocusLevel.HIGH:
        hints.append("frontend-heavy UI development")
    if chars.development_focus == FocusLevel.HIGH:
        hints.append("software engineer developer build implement")
    if chars.development_focus == FocusLevel.LOW or chars.support_operations_focus == FocusLevel.HIGH:
        hints.append("production support operations help desk")
    if chars.people_management_focus == FocusLevel.HIGH:
        hints.append("engineering manager people manager")
    if not hints:
        return job
    note = "Semantic focus hints: " + "; ".join(hints)
    description = (job.description or "") + "\n" + note
    return job.model_copy(update={"description": description})

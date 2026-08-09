"""Recommendation rules from qualification/desirability scores."""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.evaluation import Recommendation, ScoutEvaluation


@dataclass(frozen=True)
class ScoutThresholds:
    """Configurable recommendation thresholds."""

    min_qualification: int = 55
    min_desirability: int = 50
    strong_qualification: int = 80
    strong_desirability: int = 75
    maybe_qualification: int = 40
    maybe_desirability: int = 40


def apply_recommendation_rules(
    evaluation: ScoutEvaluation,
    thresholds: ScoutThresholds,
) -> ScoutEvaluation:
    """Assign recommendation enum from scores. Does not authorize anything."""
    if evaluation.recommendation == Recommendation.HARD_REJECT:
        return evaluation
    if evaluation.dealbreakers:
        evaluation.recommendation = Recommendation.HARD_REJECT
        return evaluation

    q = evaluation.qualification_score
    d = evaluation.desirability_score

    if (
        q >= thresholds.strong_qualification
        and d >= thresholds.strong_desirability
    ):
        evaluation.recommendation = Recommendation.STRONG_RECOMMEND
    elif q >= thresholds.min_qualification and d >= thresholds.min_desirability:
        evaluation.recommendation = Recommendation.RECOMMEND
    elif q >= thresholds.maybe_qualification and d >= thresholds.maybe_desirability:
        evaluation.recommendation = Recommendation.MAYBE
    else:
        evaluation.recommendation = Recommendation.DO_NOT_RECOMMEND

    return evaluation


def should_present_to_user(evaluation: ScoutEvaluation) -> bool:
    """Whether Scout should surface the job for human review.

    HARD_REJECT and DO_NOT_RECOMMEND are not presented by default.
    Presentation still never authorizes application.
    """
    return evaluation.recommendation in {
        Recommendation.STRONG_RECOMMEND,
        Recommendation.RECOMMEND,
        Recommendation.MAYBE,
    }

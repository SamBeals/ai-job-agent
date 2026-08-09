"""Deterministic aggregation of RequirementMatch → qualification score."""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.evidence import EvidenceStrength
from app.schemas.qualification import (
    MatchLevel,
    QualificationWeights,
    RequirementMatch,
    RequirementType,
    SemanticJobEvaluation,
)


@dataclass(frozen=True)
class QualificationScoreResult:
    score: int
    matches: list[RequirementMatch]
    reasoning: list[str]
    matching_skills: list[str]
    partial_matches: list[str]
    missing_required_skills: list[str]
    missing_preferred_skills: list[str]
    experience_matches: list[str]
    concerns: list[str]


def score_qualification(
    semantic: SemanticJobEvaluation,
    *,
    weights: QualificationWeights | None = None,
) -> QualificationScoreResult:
    """Aggregate structured requirement matches into an explainable 0–100 score.

    The LLM (or mock) classifies matches; this function assigns points.
    """
    weights = weights or QualificationWeights()
    matches = [m.model_copy(deep=True) for m in semantic.requirements]

    required = [m for m in matches if m.requirement.requirement_type == RequirementType.REQUIRED]
    preferred = [m for m in matches if m.requirement.requirement_type == RequirementType.PREFERRED]
    # Ignore INFERRED_CONTEXT for scoring (informational only)

    req_score, req_annotated = _pool_score(required, weights)
    pref_score, pref_annotated = _pool_score(preferred, weights)

    if not required and not preferred:
        base = weights.no_required_skills_base
        annotated = req_annotated + pref_annotated
        return QualificationScoreResult(
            score=base,
            matches=annotated,
            reasoning=[semantic.summary or "Limited structured requirements for scoring."],
            matching_skills=[],
            partial_matches=[],
            missing_required_skills=[],
            missing_preferred_skills=[],
            experience_matches=[],
            concerns=[],
        )

    if required and preferred:
        raw = (
            req_score * weights.required_pool_weight
            + pref_score * weights.preferred_pool_weight
        )
    elif required:
        raw = req_score
    else:
        raw = pref_score

    conflict_count = sum(1 for m in matches if m.match_level == MatchLevel.CONFLICT)
    missing_required_count = sum(
        1
        for m in matches
        if m.requirement.requirement_type == RequirementType.REQUIRED
        and m.match_level in {MatchLevel.NO_EVIDENCE, MatchLevel.CONFLICT}
    )
    raw -= conflict_count * weights.conflict_penalty
    raw -= missing_required_count * weights.missing_required_penalty
    score = max(0, min(100, int(round(raw))))

    annotated = req_annotated + pref_annotated
    reasoning = _build_reasoning(annotated, semantic.summary)
    matching, partial, miss_req, miss_pref, experience, concerns = _partition(annotated)

    return QualificationScoreResult(
        score=score,
        matches=annotated,
        reasoning=reasoning,
        matching_skills=matching,
        partial_matches=partial,
        missing_required_skills=miss_req,
        missing_preferred_skills=miss_pref,
        experience_matches=experience,
        concerns=concerns,
    )


def _pool_score(
    items: list[RequirementMatch],
    weights: QualificationWeights,
) -> tuple[float, list[RequirementMatch]]:
    if not items:
        return 0.0, []
    annotated: list[RequirementMatch] = []
    total_importance = sum(max(m.requirement.importance, 0.01) for m in items) or 1.0
    weighted = 0.0
    for m in items:
        points = weights.match_points.get(m.match_level.value, 0.0)
        if (
            m.evidence_strength == EvidenceStrength.LISTED_SKILL
            and points > weights.listed_skill_cap
        ):
            points = weights.listed_skill_cap
        contribution = points * 100.0 * (m.requirement.importance / total_importance)
        updated = m.model_copy(update={"contribution_points": round(contribution, 2)})
        annotated.append(updated)
        weighted += contribution
    return weighted, annotated


def _build_reasoning(matches: list[RequirementMatch], summary: str) -> list[str]:
    lines: list[str] = []
    if summary:
        lines.append(summary)
    for m in matches:
        req = m.requirement
        label = f"{req.requirement_type.value.title()} {req.name}: {m.match_level.value}"
        if m.contribution_points is not None:
            label += f" ({m.contribution_points:+.1f})"
        if m.reasoning:
            label += f" — {m.reasoning}"
        lines.append(label)
    return lines


def _partition(
    matches: list[RequirementMatch],
) -> tuple[list[str], list[str], list[str], list[str], list[str], list[str]]:
    matching: list[str] = []
    partial: list[str] = []
    miss_req: list[str] = []
    miss_pref: list[str] = []
    experience: list[str] = []
    concerns: list[str] = []

    for m in matches:
        name = m.requirement.name
        strength = m.evidence_strength.value.replace("_", " ").lower()
        if m.requirement.category.value == "EXPERIENCE" and m.match_level in {
            MatchLevel.STRONG_MATCH,
            MatchLevel.MATCH,
            MatchLevel.PARTIAL_MATCH,
        }:
            experience.append(m.reasoning or name)
            continue
        if m.match_level in {MatchLevel.STRONG_MATCH, MatchLevel.MATCH}:
            matching.append(f"{name} — {strength}")
        elif m.match_level in {MatchLevel.PARTIAL_MATCH, MatchLevel.TRANSFERABLE}:
            note = m.reasoning or strength
            partial.append(f"{name} — {note}")
        elif m.match_level == MatchLevel.NO_EVIDENCE:
            if m.requirement.requirement_type == RequirementType.REQUIRED:
                miss_req.append(name)
            elif m.requirement.requirement_type == RequirementType.PREFERRED:
                miss_pref.append(name)
            concerns.append(f"No verified evidence for {name}")
        elif m.match_level == MatchLevel.CONFLICT:
            concerns.append(f"Conflict on {name}: {m.reasoning}")
    return matching, partial, miss_req, miss_pref, experience, concerns

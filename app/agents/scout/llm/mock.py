"""Deterministic / mock Scout evaluator — used in tests and default local runs."""

from __future__ import annotations

from datetime import datetime, timezone

from app.agents.scout.desirability import score_desirability
from app.agents.scout.llm.base import DeterministicContext
from app.schemas.candidate import CandidateProfile
from app.schemas.evaluation import (
    Confidence,
    HardFilterResult,
    Recommendation,
    ScoutEvaluation,
)
from app.schemas.evidence import (
    EVIDENCE_WEIGHTS,
    EvidenceStrength,
    SkillMatchReport,
)
from app.schemas.job_posting import NormalizedJob


class MockLLMClient:
    """Score qualification and desirability from structured evidence only.

    Never calls a paid API. Never creates approvals.
    """

    provider_name = "mock"

    def __init__(self, *, evaluator_version: str = "2a.1") -> None:
        self.evaluator_version = evaluator_version

    def evaluate_job(
        self,
        candidate: CandidateProfile,
        job: NormalizedJob,
        deterministic_context: DeterministicContext,
    ) -> ScoutEvaluation:
        skill_data = deterministic_context.get("skill_report") or {}
        hard_data = deterministic_context.get("hard_filter") or {}
        skill_report = SkillMatchReport.model_validate(skill_data)
        hard_filter = HardFilterResult.model_validate(hard_data)
        version = str(
            deterministic_context.get("evaluator_version") or self.evaluator_version
        )

        if not hard_filter.passed:
            dealbreakers = [r.message for r in hard_filter.rejection_reasons]
            return ScoutEvaluation(
                qualification_score=self._qualification_score(candidate, job, skill_report),
                desirability_score=0,
                recommendation=Recommendation.HARD_REJECT,
                confidence=self._confidence(job, skill_report),
                matching_skills=_fmt_matches(skill_report),
                partial_matches=_fmt_partial(skill_report),
                missing_required_skills=list(skill_report.missing_required_skills),
                missing_preferred_skills=list(skill_report.missing_preferred_skills),
                experience_matches=list(skill_report.experience_matches),
                concerns=list(dealbreakers),
                dealbreakers=dealbreakers,
                qualification_reasoning=self._qualification_reasoning(
                    candidate, job, skill_report
                ),
                desirability_reasoning=[
                    "Hard-filtered before preference scoring.",
                    *dealbreakers,
                ],
                uncertainties=[w.message for w in hard_filter.warnings],
                hard_filter=hard_filter,
                evaluated_at=datetime.now(timezone.utc),
                evaluator_version=version,
                evaluator_provider=self.provider_name,
            )

        qual = self._qualification_score(candidate, job, skill_report)
        desire_breakdown = score_desirability(candidate.preferences, job)
        confidence = self._confidence(job, skill_report)
        qual_reason = self._qualification_reasoning(candidate, job, skill_report)
        desire_reason = list(desire_breakdown.strengths)
        if desire_breakdown.concerns:
            desire_reason.extend(
                f"Preference concern: {c}" for c in desire_breakdown.concerns
            )
        uncertainties: list[str] = []
        for item in [w.message for w in hard_filter.warnings] + desire_breakdown.unknowns:
            if item not in uncertainties:
                uncertainties.append(item)

        return ScoutEvaluation(
            qualification_score=qual,
            desirability_score=desire_breakdown.score,
            recommendation=Recommendation.MAYBE,  # pipeline applies thresholds
            confidence=confidence,
            matching_skills=_fmt_matches(skill_report),
            partial_matches=_fmt_partial(skill_report),
            missing_required_skills=list(skill_report.missing_required_skills),
            missing_preferred_skills=list(skill_report.missing_preferred_skills),
            experience_matches=list(skill_report.experience_matches),
            concerns=self._concerns(skill_report) + list(desire_breakdown.concerns),
            dealbreakers=[],
            qualification_reasoning=qual_reason,
            desirability_reasoning=desire_reason,
            uncertainties=uncertainties,
            hard_filter=hard_filter,
            evaluated_at=datetime.now(timezone.utc),
            evaluator_version=version,
            evaluator_provider=self.provider_name,
        )

    def _qualification_score(
        self,
        candidate: CandidateProfile,
        job: NormalizedJob,
        report: SkillMatchReport,
    ) -> int:
        required = job.required_skills
        preferred = job.preferred_skills

        if not required and not preferred:
            # Vague posting — modest score from experience themes only
            base = 50
            if report.experience_matches:
                base += 15
            return _clamp(base)

        req_score = 0.0
        if required:
            evidence_map = {_norm(e.skill): e for e in report.matching_skills}
            weights = []
            for skill in required:
                ev = evidence_map.get(_norm(skill))
                if ev is None:
                    weights.append(0.0)
                else:
                    weights.append(EVIDENCE_WEIGHTS.get(ev.strength, 0.0))
            req_score = (sum(weights) / len(weights)) * 100.0
        else:
            req_score = 70.0

        pref_penalty = 0.0
        if preferred:
            missing_pref = len(report.missing_preferred_skills)
            # Prefer gaps hurt less than required gaps
            pref_penalty = (missing_pref / max(len(preferred), 1)) * 12.0

        required_missing_penalty = 0.0
        if required:
            missing_req = len(report.missing_required_skills)
            required_missing_penalty = (missing_req / len(required)) * 55.0

        years_bonus = 0.0
        years = candidate.approximate_years_of_experience()
        if years is not None and job.required_years_experience is not None:
            if years >= job.required_years_experience:
                years_bonus = 8.0
            elif years >= job.required_years_experience - 1:
                years_bonus = 3.0
            else:
                required_missing_penalty += 12.0

        edu_bonus = 0.0
        if job.education_requirements:
            if _education_satisfied(candidate, job.education_requirements):
                edu_bonus = 6.0
            else:
                required_missing_penalty += 10.0

        # Blend: required alignment dominates
        score = req_score - required_missing_penalty - pref_penalty + years_bonus + edu_bonus
        if report.experience_matches:
            score += 4.0
        return _clamp(score)

    def _confidence(self, job: NormalizedJob, report: SkillMatchReport) -> Confidence:
        points = 0
        if job.required_skills:
            points += 2
        if job.preferred_skills:
            points += 1
        if job.salary_min is not None or job.salary_max is not None:
            points += 1
        if job.remote_status:
            points += 1
        if job.location:
            points += 1
        if job.responsibilities or (job.description and len(job.description) > 120):
            points += 1
        if job.required_years_experience is not None:
            points += 1
        if job.education_requirements:
            points += 1
        if points >= 7:
            return Confidence.HIGH
        if points >= 4:
            return Confidence.MEDIUM
        return Confidence.LOW

    def _qualification_reasoning(
        self,
        candidate: CandidateProfile,
        job: NormalizedJob,
        report: SkillMatchReport,
    ) -> list[str]:
        lines: list[str] = []
        for ev in report.matching_skills:
            label = ev.strength.value.replace("_", " ").title()
            detail = f" ({ev.source_detail})" if ev.source_detail else ""
            lines.append(f"Strong {ev.skill} alignment — {label}{detail}.")
        for ev in report.partial_matches:
            note = ev.notes or "partial evidence"
            lines.append(f"{ev.skill} — {note}.")
        for skill in report.missing_required_skills:
            lines.append(f"No verified evidence found for required skill: {skill}.")
        for skill in report.missing_preferred_skills:
            lines.append(f"Preferred skill gap: {skill}.")
        years = candidate.approximate_years_of_experience()
        if years is not None:
            if job.required_years_experience is not None:
                lines.append(
                    f"Approximate professional experience ~{years} years "
                    f"(role asks {job.required_years_experience}+)."
                )
            else:
                lines.append(f"Approximate professional experience ~{years} years.")
        if not lines:
            lines.append("Limited structured requirements available for qualification scoring.")
        return lines

    def _concerns(self, report: SkillMatchReport) -> list[str]:
        concerns: list[str] = []
        for skill in report.missing_required_skills:
            concerns.append(f"Missing required skill evidence: {skill}")
        for ev in report.partial_matches:
            if ev.strength == EvidenceStrength.LISTED_SKILL:
                concerns.append(f"{ev.skill}: listed skill only; depth unknown")
        return concerns


def _clamp(value: float) -> int:
    return max(0, min(100, int(round(value))))


def _norm(value: str) -> str:
    from app.agents.scout.skills import normalize_skill

    return normalize_skill(value)


def _fmt_matches(report: SkillMatchReport) -> list[str]:
    out = []
    for ev in report.matching_skills:
        strength = ev.strength.value.replace("_", " ").lower()
        out.append(f"{ev.skill} — {strength}")
    return out


def _fmt_partial(report: SkillMatchReport) -> list[str]:
    out = []
    for ev in report.partial_matches:
        note = ev.notes or ev.strength.value.lower()
        out.append(f"{ev.skill} — {note}")
    return out


def _education_satisfied(candidate: CandidateProfile, requirements: list[str]) -> bool:
    blob = " ".join(
        f"{e.degree} {e.field or ''} {e.status or ''}" for e in candidate.education
    ).lower()
    for req in requirements:
        r = req.lower()
        if "bachelor" in r or "bs" in r or "b.s" in r or "computer science" in r:
            if "bachelor" in blob or "computer science" in blob:
                continue
            return False
        if "master" in r or "phd" in r or "ph.d" in r:
            # Only count completed graduate degrees
            completed = any(
                (e.status or "").lower() == "completed"
                and (
                    "master" in (e.degree or "").lower()
                    or "phd" in (e.degree or "").lower()
                )
                for e in candidate.education
            )
            if "phd" in r or "ph.d" in r:
                return False  # candidate has no PhD fact
            if "master" in r and not completed:
                return False
    return True

"""Build deterministic SemanticJobEvaluation from SkillMatchReport (mock path)."""

from __future__ import annotations

from app.schemas.candidate import CandidateProfile
from app.schemas.evidence import EvidenceStrength, SkillMatchReport
from app.schemas.job_posting import NormalizedJob
from app.schemas.qualification import (
    FocusLevel,
    JobCharacteristics,
    JobRequirement,
    MatchLevel,
    RequirementCategory,
    RequirementMatch,
    RequirementType,
    SemanticJobEvaluation,
)


def build_deterministic_semantic_evaluation(
    candidate: CandidateProfile,
    job: NormalizedJob,
    skill_report: SkillMatchReport,
) -> SemanticJobEvaluation:
    """Map evidence matcher output into requirement-level matches (no LLM)."""
    matches: list[RequirementMatch] = []

    evidence_by_skill = {e.skill.lower(): e for e in skill_report.matching_skills}
    partial_by_skill = {e.skill.lower(): e for e in skill_report.partial_matches}

    for idx, skill in enumerate(job.required_skills):
        matches.append(
            _skill_match(
                skill,
                RequirementType.REQUIRED,
                f"req-skill-{idx}",
                evidence_by_skill,
                partial_by_skill,
                skill in skill_report.missing_required_skills,
            )
        )
    for idx, skill in enumerate(job.preferred_skills):
        matches.append(
            _skill_match(
                skill,
                RequirementType.PREFERRED,
                f"pref-skill-{idx}",
                evidence_by_skill,
                partial_by_skill,
                skill in skill_report.missing_preferred_skills,
            )
        )

    # Years of professional experience (not technology-specific years)
    years = candidate.approximate_years_of_experience()
    if job.required_years_experience is not None:
        if years is not None and years >= job.required_years_experience:
            level = MatchLevel.STRONG_MATCH
            reasoning = (
                f"Approximately {years} years professional software engineering "
                f"(role asks {job.required_years_experience}+)."
            )
            evidence = [reasoning]
        elif years is not None:
            level = MatchLevel.PARTIAL_MATCH
            reasoning = (
                f"Approximately {years} years professional experience; "
                f"role asks {job.required_years_experience}+."
            )
            evidence = [reasoning]
        else:
            level = MatchLevel.UNKNOWN
            reasoning = "Professional years could not be derived from employment dates."
            evidence = []
        matches.append(
            RequirementMatch(
                requirement=JobRequirement(
                    id="req-years",
                    name=f"{job.required_years_experience}+ years software engineering",
                    category=RequirementCategory.EXPERIENCE,
                    requirement_type=RequirementType.REQUIRED,
                    minimum_years=job.required_years_experience,
                ),
                match_level=level,
                evidence_strength=EvidenceStrength.PROFESSIONAL_EXPERIENCE
                if years is not None
                else EvidenceStrength.UNKNOWN,
                candidate_evidence=evidence,
                reasoning=reasoning,
                confidence="HIGH" if years is not None else "LOW",
            )
        )

    for idx, edu_req in enumerate(job.education_requirements):
        matches.append(_education_match(candidate, edu_req, f"req-edu-{idx}"))

    characteristics = _infer_characteristics(job)
    summary = (
        "Deterministic evidence-based qualification analysis from structured skill matching."
    )
    uncertainties: list[str] = []
    if not job.required_skills and not job.preferred_skills:
        uncertainties.append("Job posting lists few structured skill requirements.")
    if job.remote_status is None:
        uncertainties.append("Work arrangement unknown from posting.")
    if job.location is None:
        uncertainties.append("Location unknown from posting.")
    if job.salary_min is None and job.salary_max is None:
        uncertainties.append("Salary unknown from posting.")

    overall = "HIGH" if job.required_skills else "MEDIUM"
    if job.remote_status is None or job.location is None or (
        job.salary_min is None and job.salary_max is None
    ):
        overall = "MEDIUM"

    return SemanticJobEvaluation(
        requirements=matches,
        job_characteristics=characteristics,
        summary=summary,
        uncertainties=uncertainties,
        overall_confidence=overall,
    )


def _skill_match(
    skill: str,
    req_type: RequirementType,
    req_id: str,
    evidence_by_skill: dict,
    partial_by_skill: dict,
    is_missing: bool,
) -> RequirementMatch:
    req = JobRequirement(
        id=req_id,
        name=skill,
        category=RequirementCategory.SKILL,
        requirement_type=req_type,
    )
    key = skill.lower()
    if key in evidence_by_skill:
        ev = evidence_by_skill[key]
        level = _level_from_strength(ev.strength)
        return RequirementMatch(
            requirement=req,
            match_level=level,
            evidence_strength=ev.strength,
            candidate_evidence=[ev.source_detail] if ev.source_detail else [],
            reasoning=f"{skill} supported by {ev.strength.value.replace('_', ' ').lower()}.",
            confidence="HIGH"
            if ev.strength == EvidenceStrength.PROFESSIONAL_EXPERIENCE
            else "MEDIUM",
        )
    if key in partial_by_skill:
        ev = partial_by_skill[key]
        return RequirementMatch(
            requirement=req,
            match_level=MatchLevel.PARTIAL_MATCH,
            evidence_strength=ev.strength,
            candidate_evidence=[ev.source_detail or ev.notes or ""],
            reasoning=ev.notes or f"Partial evidence for {skill}.",
            confidence="MEDIUM",
        )

    # Conservative transferable heuristics (still preserve technology gaps elsewhere)
    transferable = _transferable_from_catalog(skill, evidence_by_skill)
    if transferable is not None:
        return transferable(req)

    return RequirementMatch(
        requirement=req,
        match_level=MatchLevel.NO_EVIDENCE,
        evidence_strength=EvidenceStrength.UNKNOWN,
        candidate_evidence=[],
        reasoning=f"No verified candidate evidence found for {skill}.",
        confidence="HIGH",
    )


def _transferable_from_catalog(skill: str, evidence_by_skill: dict):
    """Return a RequirementMatch factory for a few safe transferable cases."""
    key = skill.lower()
    oop_terms = ("object-oriented", "object oriented", "oop")
    if any(t in key for t in oop_terms):
        for lang in ("java", "python", "c#", "c++"):
            if lang in evidence_by_skill:
                ev = evidence_by_skill[lang]

                def _build(req, _ev=ev, _lang=lang):
                    return RequirementMatch(
                        requirement=req,
                        match_level=MatchLevel.TRANSFERABLE,
                        evidence_strength=_ev.strength,
                        candidate_evidence=[_ev.source_detail or _lang],
                        reasoning=(
                            f"Transferable: professional {_lang} experience supports "
                            "object-oriented programming; not a separate listed skill."
                        ),
                        confidence="MEDIUM",
                    )

                return _build
    return None


def _level_from_strength(strength: EvidenceStrength) -> MatchLevel:
    if strength == EvidenceStrength.PROFESSIONAL_EXPERIENCE:
        return MatchLevel.STRONG_MATCH
    if strength == EvidenceStrength.PROJECT:
        return MatchLevel.MATCH
    if strength in {EvidenceStrength.EDUCATION, EvidenceStrength.CERTIFICATION}:
        return MatchLevel.PARTIAL_MATCH
    if strength == EvidenceStrength.LISTED_SKILL:
        return MatchLevel.PARTIAL_MATCH
    return MatchLevel.UNKNOWN


def _education_match(candidate: CandidateProfile, edu_req: str, req_id: str) -> RequirementMatch:
    req = JobRequirement(
        id=req_id,
        name=edu_req,
        category=RequirementCategory.EDUCATION,
        requirement_type=RequirementType.REQUIRED,
    )
    blob = " ".join(
        f"{e.degree} {e.field or ''} {e.status or ''}" for e in candidate.education
    ).lower()
    r = edu_req.lower()
    if "phd" in r or "ph.d" in r:
        return RequirementMatch(
            requirement=req,
            match_level=MatchLevel.NO_EVIDENCE,
            reasoning="No verified PhD found in candidate education.",
            confidence="HIGH",
        )
    if "master" in r:
        completed = any(
            (e.status or "").lower() == "completed" and "master" in (e.degree or "").lower()
            for e in candidate.education
        )
        if completed:
            return RequirementMatch(
                requirement=req,
                match_level=MatchLevel.STRONG_MATCH,
                evidence_strength=EvidenceStrength.EDUCATION,
                candidate_evidence=["Completed master's degree"],
                reasoning="Verified completed master's degree.",
                confidence="HIGH",
            )
        return RequirementMatch(
            requirement=req,
            match_level=MatchLevel.NO_EVIDENCE,
            reasoning="No completed master's degree on record (in-progress does not count as complete).",
            confidence="HIGH",
        )
    if "bachelor" in r or "computer science" in r or "b.s" in r:
        if "bachelor" in blob or "computer science" in blob:
            return RequirementMatch(
                requirement=req,
                match_level=MatchLevel.STRONG_MATCH,
                evidence_strength=EvidenceStrength.EDUCATION,
                candidate_evidence=[edu.degree + (f" — {edu.field}" if edu.field else "") for edu in candidate.education if edu.status == "completed" or edu.graduation_date][:2] or ["Bachelor's / CS education present"],
                reasoning="Verified bachelor's / computer science education.",
                confidence="HIGH",
            )
    return RequirementMatch(
        requirement=req,
        match_level=MatchLevel.UNKNOWN,
        reasoning="Education requirement could not be confidently matched.",
        confidence="LOW",
    )


def _infer_characteristics(job: NormalizedJob) -> JobCharacteristics:
    text = " ".join(
        [
            job.title or "",
            job.description or "",
            " ".join(job.responsibilities or []),
            " ".join(job.required_skills or []),
        ]
    ).lower()
    backend = FocusLevel.UNKNOWN
    frontend = FocusLevel.UNKNOWN
    development = FocusLevel.UNKNOWN
    support = FocusLevel.UNKNOWN

    if any(x in text for x in ("backend", "spring boot", "api", "java", "microservices")):
        backend = FocusLevel.HIGH
    if any(x in text for x in ("frontend", "react", "css", "html", "ui ")):
        frontend = FocusLevel.HIGH
    if any(x in text for x in ("software engineer", "developer", "develop", "build")):
        development = FocusLevel.HIGH
    if any(x in text for x in ("production support", "help desk", "operations support")):
        support = FocusLevel.HIGH
        development = FocusLevel.LOW if development == FocusLevel.UNKNOWN else development

    return JobCharacteristics(
        development_focus=development,
        backend_focus=backend,
        frontend_focus=frontend,
        support_operations_focus=support,
    )

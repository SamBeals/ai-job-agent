"""Evidence-based skill matching against a candidate profile."""

from __future__ import annotations

from app.agents.scout.skills import (
    normalize_skill,
    skills_equivalent,
    skills_partially_related,
)
from app.schemas.candidate import CandidateProfile
from app.schemas.evidence import (
    EvidenceStrength,
    SkillEvidence,
    SkillMatchReport,
)
from app.schemas.job_posting import NormalizedJob


def match_skills(candidate: CandidateProfile, job: NormalizedJob) -> SkillMatchReport:
    """Match job required/preferred skills to candidate evidence.

    Professional experience evidence outranks generic listed skills.
    """
    catalog = _build_candidate_skill_catalog(candidate)
    matching: list[SkillEvidence] = []
    partial: list[SkillEvidence] = []
    missing_required: list[str] = []
    missing_preferred: list[str] = []
    seen_required: set[str] = set()

    for skill in job.required_skills:
        evidence = _best_evidence(skill, catalog)
        key = normalize_skill(skill)
        if evidence is None:
            # try partial relatedness
            related = _best_partial(skill, catalog)
            if related is not None:
                partial.append(related)
            missing_required.append(skill)
        else:
            matching.append(evidence)
            seen_required.add(key)

    for skill in job.preferred_skills:
        key = normalize_skill(skill)
        if key in seen_required:
            continue
        evidence = _best_evidence(skill, catalog)
        if evidence is None:
            related = _best_partial(skill, catalog)
            if related is not None:
                partial.append(related)
            else:
                missing_preferred.append(skill)
        elif evidence.strength == EvidenceStrength.LISTED_SKILL:
            partial.append(
                SkillEvidence(
                    skill=skill,
                    matched_candidate_skill=evidence.matched_candidate_skill,
                    strength=evidence.strength,
                    source_detail=evidence.source_detail,
                    notes="Verified listed skill; depth unknown",
                )
            )
        else:
            matching.append(evidence)

    experience_matches = _experience_theme_matches(candidate, job)

    return SkillMatchReport(
        matching_skills=matching,
        partial_matches=_dedupe_evidence(partial),
        missing_required_skills=missing_required,
        missing_preferred_skills=missing_preferred,
        experience_matches=experience_matches,
    )


def _build_candidate_skill_catalog(
    candidate: CandidateProfile,
) -> list[tuple[str, EvidenceStrength, str]]:
    """Return (skill_name, strength, source_detail) entries."""
    catalog: list[tuple[str, EvidenceStrength, str]] = []

    for exp in candidate.work_experience:
        detail_prefix = f"{exp.title} at {exp.company}"
        for tech in exp.technologies:
            catalog.append(
                (
                    tech,
                    EvidenceStrength.PROFESSIONAL_EXPERIENCE,
                    detail_prefix,
                )
            )
        # Also scan accomplishments for explicit technology mentions already listed
        for accomplishment in exp.verified_accomplishments:
            for tech in exp.technologies:
                if tech.lower() in accomplishment.lower():
                    catalog.append(
                        (
                            tech,
                            EvidenceStrength.PROFESSIONAL_EXPERIENCE,
                            f"{detail_prefix}: {accomplishment[:160]}",
                        )
                    )

    for project in candidate.projects:
        for tech in project.technologies:
            catalog.append(
                (
                    tech,
                    EvidenceStrength.PROJECT,
                    f"Project: {project.name}",
                )
            )

    for edu in candidate.education:
        field = edu.field or edu.degree
        if field:
            catalog.append(
                (
                    field,
                    EvidenceStrength.EDUCATION,
                    f"{edu.degree} — {edu.institution}",
                )
            )

    for cert in candidate.certifications:
        catalog.append(
            (
                cert.name,
                EvidenceStrength.CERTIFICATION,
                cert.name,
            )
        )

    for skill in candidate.skills.all_skills():
        strength = skill.evidence_type or EvidenceStrength.LISTED_SKILL
        # Listed inventory should not outrank professional evidence already present
        if strength == EvidenceStrength.PROFESSIONAL_EXPERIENCE:
            strength = EvidenceStrength.LISTED_SKILL
        catalog.append(
            (
                skill.name,
                strength if strength != EvidenceStrength.UNKNOWN else EvidenceStrength.LISTED_SKILL,
                f"Listed skill (source={skill.source})",
            )
        )

    return catalog


def _best_evidence(
    required: str,
    catalog: list[tuple[str, EvidenceStrength, str]],
) -> SkillEvidence | None:
    best: SkillEvidence | None = None
    best_rank = -1
    order = {
        EvidenceStrength.PROFESSIONAL_EXPERIENCE: 5,
        EvidenceStrength.PROJECT: 4,
        EvidenceStrength.EDUCATION: 3,
        EvidenceStrength.CERTIFICATION: 2,
        EvidenceStrength.LISTED_SKILL: 1,
        EvidenceStrength.UNKNOWN: 0,
    }
    for name, strength, detail in catalog:
        if not skills_equivalent(required, name):
            continue
        rank = order[strength]
        if rank > best_rank:
            best_rank = rank
            best = SkillEvidence(
                skill=required,
                matched_candidate_skill=name,
                strength=strength,
                source_detail=detail,
            )
    return best


def _best_partial(
    required: str,
    catalog: list[tuple[str, EvidenceStrength, str]],
) -> SkillEvidence | None:
    for name, strength, detail in catalog:
        if skills_partially_related(required, name):
            return SkillEvidence(
                skill=required,
                matched_candidate_skill=name,
                strength=strength,
                source_detail=detail,
                notes="Related technology; not an exact match",
            )
    return None


def _experience_theme_matches(candidate: CandidateProfile, job: NormalizedJob) -> list[str]:
    matches: list[str] = []
    title_l = job.title.lower()
    for exp in candidate.work_experience:
        exp_title = exp.title.lower()
        if any(
            token in title_l and token in exp_title
            for token in ("engineer", "software", "product", "backend", "platform")
        ):
            matches.append(f"{exp.title} at {exp.company} ({exp.start_date})")
    # Years
    years = candidate.approximate_years_of_experience()
    if years is not None and job.required_years_experience is not None:
        if years >= job.required_years_experience:
            matches.append(
                f"Approximate professional experience {years}+ years "
                f"(requirement {job.required_years_experience}+)"
            )
    return matches


def _dedupe_evidence(items: list[SkillEvidence]) -> list[SkillEvidence]:
    seen: set[str] = set()
    out: list[SkillEvidence] = []
    for item in items:
        key = normalize_skill(item.skill)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def listed_skill_names(candidate: CandidateProfile) -> set[str]:
    return {normalize_skill(s.name) for s in candidate.skills.all_skills()}

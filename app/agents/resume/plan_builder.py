"""Deterministic ResumePlan builder from Scout evaluation + candidate facts."""

from __future__ import annotations

from app.schemas.candidate import CandidateProfile
from app.schemas.evaluation import ScoutEvaluation
from app.schemas.job_posting import NormalizedJob
from app.schemas.qualification import MatchLevel
from app.schemas.resume_plan import ResumePlan, ResumePlanItem


AGENT_VERSION = "3.0.0"


def build_resume_plan(
    *,
    candidate: CandidateProfile,
    job: NormalizedJob,
    evaluation: ScoutEvaluation,
    job_id: int,
    pipeline_id: int,
    scout_evaluation_id: int | None = None,
) -> ResumePlan:
    """Build a structured ResumePlan without inventing candidate facts."""
    emphasize: list[ResumePlanItem] = []
    secondary: list[ResumePlanItem] = []
    transferable: list[ResumePlanItem] = []
    not_claim: list[str] = []
    gaps: list[str] = []
    accomplishments: list[ResumePlanItem] = []
    experience_items: list[ResumePlanItem] = []

    for raw in evaluation.requirement_matches:
        req = raw.get("requirement") or {}
        name = str(req.get("name") or "").strip()
        if not name:
            continue
        level = str(raw.get("match_level") or "")
        strength = str(raw.get("evidence_strength") or "UNKNOWN")
        evidence = list(raw.get("candidate_evidence") or [])
        reasoning = str(raw.get("reasoning") or "")
        category = str(req.get("category") or "SKILL")

        if level in {MatchLevel.STRONG_MATCH.value, MatchLevel.MATCH.value}:
            item = ResumePlanItem(
                text=name,
                category="skill" if category == "SKILL" else category.lower(),
                evidence_strength=strength,
                evidence_refs=[f"scout:{name}"] + [f"evidence:{e[:80]}" for e in evidence[:2]],
                source_detail=reasoning or (evidence[0] if evidence else None),
            )
            if category == "EXPERIENCE":
                experience_items.append(item)
            else:
                emphasize.append(item)
        elif level in {MatchLevel.PARTIAL_MATCH.value, MatchLevel.TRANSFERABLE.value}:
            secondary.append(
                ResumePlanItem(
                    text=name,
                    category="skill",
                    evidence_strength=strength,
                    evidence_refs=[f"scout:{name}"],
                    source_detail=reasoning or "Partial / transferable evidence only",
                )
            )
            if level == MatchLevel.TRANSFERABLE.value:
                transferable.append(
                    ResumePlanItem(
                        text=name,
                        category="skill",
                        evidence_strength=strength,
                        evidence_refs=[f"scout:{name}"],
                        source_detail=reasoning,
                    )
                )
        elif level == MatchLevel.NO_EVIDENCE.value:
            not_claim.append(name)
            gaps.append(f"No verified evidence for {name}")

    # Experience ordering from verified work history (current first)
    ordering: list[str] = []
    for work in candidate.work_experience:
        label = f"{work.company} — {work.title}"
        ordering.append(label)
        experience_items.append(
            ResumePlanItem(
                text=label,
                category="experience",
                evidence_strength="PROFESSIONAL_EXPERIENCE",
                evidence_refs=[f"employer:{work.company}"],
                source_detail=work.start_date,
            )
        )
        for acc in work.verified_accomplishments[:3]:
            accomplishments.append(
                ResumePlanItem(
                    text=acc,
                    category="accomplishment",
                    evidence_strength="PROFESSIONAL_EXPERIENCE",
                    evidence_refs=[f"employer:{work.company}"],
                    source_detail=work.company,
                )
            )

    years = candidate.approximate_years_of_experience()
    years_note = (
        f"approximately {years:.0f} years professional software engineering"
        if years is not None
        else "professional software engineering experience"
    )
    summary = (
        f"Position {candidate.identity.full_name} as an experienced backend-focused "
        f"software engineer with {years_note}, emphasizing verified "
        f"{job.title} alignment without claiming unsupported technologies."
    )

    notes = [
        "ResumePlan is a structuring artifact — not a submitted application.",
        "Skills in skills_not_to_claim must never appear as invented experience.",
        f"Scout qualification={evaluation.qualification_score} "
        f"desirability={evaluation.desirability_score}",
    ]

    # Deduplicate not_claim while preserving order
    seen: set[str] = set()
    unique_not_claim: list[str] = []
    for s in not_claim:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            unique_not_claim.append(s)

    return ResumePlan(
        job_id=job_id,
        pipeline_id=pipeline_id,
        target_title=job.title,
        company=job.company,
        summary_strategy=summary,
        priority_experience=experience_items[:8],
        priority_skills=emphasize[:12],
        priority_accomplishments=accomplishments[:10],
        requirements_to_emphasize=emphasize[:12],
        transferable_experience=transferable[:8],
        secondary_skills=secondary[:8],
        skills_not_to_claim=unique_not_claim,
        gaps=gaps[:12],
        notes=notes,
        experience_ordering=ordering,
        agent_version=AGENT_VERSION,
        scout_evaluation_id=scout_evaluation_id,
    )

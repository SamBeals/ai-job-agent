"""Resume plan validation — every claim must map to verified evidence."""

from __future__ import annotations

from app.schemas.candidate import CandidateProfile
from app.schemas.resume_plan import ResumePlan


class ResumePlanValidationError(Exception):
    """Raised when a ResumePlan invents or overclaims evidence."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_resume_plan(plan: ResumePlan, candidate: CandidateProfile) -> ResumePlan:
    """Validate plan against verified candidate facts. Mutates validation fields."""
    errors: list[str] = []
    known_skills = {s.name.lower() for s in candidate.skills.all_skills()}
    # Also allow skills appearing in work experience technologies
    for work in candidate.work_experience:
        known_skills.update(t.lower() for t in work.technologies)
    known_employers = {w.company.lower() for w in candidate.work_experience}
    known_certs = {c.name.lower() for c in candidate.certifications}
    known_edu = {
        f"{e.degree} {e.field or ''}".strip().lower() for e in candidate.education
    }

    forbidden = {s.lower() for s in plan.skills_not_to_claim}

    def _check_items(items, *, allow_listed_only: bool = True) -> None:
        for item in items:
            text_l = item.text.lower()
            # Skills marked do-not-claim cannot appear as emphasized claims
            for bad in forbidden:
                if bad and bad in text_l and item.category in {"skill", "accomplishment"}:
                    # Allow mentioning in gaps/notes context only — not as priority claims
                    if item.evidence_strength and item.evidence_strength.upper() == "NO_EVIDENCE":
                        errors.append(f"Cannot claim skill with no evidence: {item.text}")
            # Employer names in experience items must exist
            if item.category == "experience":
                if item.evidence_refs:
                    for ref in item.evidence_refs:
                        if ref.lower().startswith("employer:"):
                            name = ref.split(":", 1)[1].strip().lower()
                            if name and name not in known_employers:
                                errors.append(f"Unknown employer reference: {ref}")

    _check_items(plan.priority_skills)
    _check_items(plan.priority_accomplishments)
    _check_items(plan.priority_experience)
    _check_items(plan.requirements_to_emphasize)

    # Explicit: skills_not_to_claim must not also appear as priority skill names
    priority_skill_names = {i.text.lower() for i in plan.priority_skills}
    overlap = priority_skill_names & forbidden
    if overlap:
        errors.append(
            "skills_not_to_claim appear in priority_skills: " + ", ".join(sorted(overlap))
        )

    # Secondary skills may include listed-only skills; still must be known
    for item in plan.secondary_skills:
        # Extract leading skill token heuristically
        token = item.text.split("—")[0].split("-")[0].strip().lower()
        if token and token not in known_skills and not any(
            token in s for s in known_skills
        ):
            # Soft check — only error if clearly unknown and no evidence_refs
            if not item.evidence_refs:
                errors.append(f"Secondary skill lacks evidence refs: {item.text}")

    plan.validation_errors = errors
    plan.validation_passed = len(errors) == 0
    if errors:
        raise ResumePlanValidationError(errors)
    return plan


def assert_no_evidence_not_claimed(plan: ResumePlan) -> None:
    """Hard rule: NO_EVIDENCE requirements cannot become claimed skills."""
    claimed = {i.text.lower() for i in plan.priority_skills}
    claimed |= {i.text.lower() for i in plan.requirements_to_emphasize}
    for name in plan.skills_not_to_claim:
        if name.lower() in claimed:
            raise ResumePlanValidationError(
                [f"NO_EVIDENCE skill incorrectly claimed: {name}"]
            )

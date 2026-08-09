"""Scout Agent — evaluates supplied jobs; does not authorize applications.

Phase 2A: candidate intelligence + evaluation foundation.
Does NOT discover jobs from the internet.
Does NOT approve jobs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.agents.scout.pipeline import ScoutPipeline, ScoutPipelineResult
from app.agents.scout.profile_loader import load_candidate_profile
from app.config import Settings, get_settings
from app.schemas.candidate import CandidateProfile
from app.schemas.evaluation import ScoutEvaluation
from app.schemas.job_posting import NormalizedJob


@dataclass
class ScoutResult:
    """Result from a scout run over supplied jobs."""

    jobs_found: int = 0
    jobs_recommended: int = 0
    evaluations: list[ScoutEvaluation] = field(default_factory=list)
    notes: str = ""


class ScoutAgent:
    """Evaluates jobs against the candidate profile.

    Authorization boundary: this agent never calls ApprovalService and never
    sets job status to APPROVED.
    """

    def __init__(
        self,
        candidate_profile_path: str | None = None,
        *,
        settings: Settings | None = None,
        session: Session | None = None,
        pipeline: ScoutPipeline | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.candidate_profile_path = (
            candidate_profile_path or self.settings.candidate_profile_path
        )
        self.session = session
        self.pipeline = pipeline or ScoutPipeline(
            settings=self.settings,
            session=session,
        )

    def load_profile(self) -> CandidateProfile:
        return load_candidate_profile(self.candidate_profile_path)

    def evaluate_job(
        self,
        job: NormalizedJob,
        *,
        candidate: CandidateProfile | None = None,
        persist: bool = False,
    ) -> ScoutPipelineResult:
        profile = candidate or self.load_profile()
        return self.pipeline.evaluate(
            job,
            profile,
            persist=persist,
            create_job_record=persist,
        )

    def run(self, jobs: list[NormalizedJob] | None = None) -> ScoutResult:
        """Evaluate supplied jobs. Autonomous discovery is not implemented."""
        if not jobs:
            return ScoutResult(
                notes=(
                    "ScoutAgent Phase 2A: no jobs supplied. "
                    "Use evaluate_job() or `python -m app.agents.scout.evaluate_job` "
                    "with a fixture. Autonomous job-board discovery is not enabled."
                )
            )

        profile = self.load_profile()
        evaluations: list[ScoutEvaluation] = []
        recommended = 0
        for job in jobs:
            result = self.evaluate_job(job, candidate=profile, persist=False)
            evaluations.append(result.evaluation)
            if result.should_present:
                recommended += 1

        return ScoutResult(
            jobs_found=len(jobs),
            jobs_recommended=recommended,
            evaluations=evaluations,
            notes="Evaluated supplied jobs only; no autonomous discovery.",
        )

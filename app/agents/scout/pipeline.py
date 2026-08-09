"""Scout evaluation pipeline — evaluate a supplied job without authorizing it."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agents.scout.evidence_matcher import match_skills
from app.agents.scout.hard_filters import apply_hard_filters
from app.agents.scout.llm.base import LLMClient, build_deterministic_context
from app.agents.scout.llm.factory import get_llm_client
from app.agents.scout.llm.mock import MockLLMClient
from app.agents.scout.llm.openai_client import EvaluatorOutputError
from app.agents.scout.profile_loader import CandidateProfileError, load_candidate_profile
from app.agents.scout.scoring import (
    ScoutThresholds,
    apply_recommendation_rules,
    should_present_to_user,
)
from app.config import Settings, get_settings
from app.models.job import Job, JobStatus
from app.schemas.candidate import CandidateProfile
from app.schemas.evaluation import Recommendation, ScoutEvaluation
from app.schemas.job_posting import NormalizedJob
from app.services.job_service import JobService
from app.services.scout_evaluation_service import ScoutEvaluationService

logger = logging.getLogger(__name__)


class ScoutEvaluationError(Exception):
    """Raised when Scout cannot safely produce an evaluation."""


@dataclass
class ScoutPipelineResult:
    """Outcome of evaluating one job. Never implies authorization."""

    evaluation: ScoutEvaluation
    job: Job | None
    persisted_evaluation_id: int | None
    should_present: bool


class ScoutPipeline:
    """
    Job Input → Normalize → Hard Filters → Evidence Match → Evaluator
    → Validate → Recommendation Rules → optional Persist

    Scout may recommend. Scout may NOT approve.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        llm_client: LLMClient | None = None,
        thresholds: ScoutThresholds | None = None,
        session: Session | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm_client = llm_client or get_llm_client(self.settings)
        self.thresholds = thresholds or ScoutThresholds(
            min_qualification=self.settings.scout_min_qualification_score,
            min_desirability=self.settings.scout_min_desirability_score,
            strong_qualification=self.settings.scout_strong_qualification_score,
            strong_desirability=self.settings.scout_strong_desirability_score,
            maybe_qualification=self.settings.scout_maybe_qualification_score,
            maybe_desirability=self.settings.scout_maybe_desirability_score,
        )
        self.session = session

    def evaluate(
        self,
        job: NormalizedJob,
        candidate: CandidateProfile,
        *,
        persist: bool = False,
        create_job_record: bool = False,
        job_id: int | None = None,
    ) -> ScoutPipelineResult:
        """Evaluate a normalized job against the candidate profile."""
        logger.info(
            "scout_evaluation_started company=%s title=%s provider=%s",
            job.company,
            job.title,
            getattr(self.llm_client, "provider_name", "unknown"),
        )

        hard_filter = apply_hard_filters(candidate, job)
        logger.info(
            "scout_hard_filter passed=%s rejections=%s warnings=%s",
            hard_filter.passed,
            [r.code for r in hard_filter.rejection_reasons],
            [w.code for w in hard_filter.warnings],
        )

        skill_report = match_skills(candidate, job)
        context = build_deterministic_context(
            skill_report=skill_report,
            hard_filter=hard_filter,
            evaluator_version=self.settings.scout_evaluator_version,
        )

        try:
            raw = self.llm_client.evaluate_job(candidate, job, context)
            evaluation = ScoutEvaluation.model_validate(raw.model_dump())
        except (ValidationError, EvaluatorOutputError, ValueError, TypeError) as exc:
            logger.exception("scout_evaluation_failed company=%s title=%s", job.company, job.title)
            raise ScoutEvaluationError(
                f"Evaluator failed safely (no fabricated recommendation): {exc}"
            ) from exc

        evaluation = apply_recommendation_rules(evaluation, self.thresholds)
        evaluation.job_id = job_id
        evaluation.evaluator_version = self.settings.scout_evaluator_version

        logger.info(
            "scout_evaluation_success recommendation=%s qualification=%s "
            "desirability=%s confidence=%s",
            evaluation.recommendation.value,
            evaluation.qualification_score,
            evaluation.desirability_score,
            evaluation.confidence.value,
        )

        db_job: Job | None = None
        persisted_id: int | None = None

        if persist or create_job_record:
            if self.session is None:
                raise ScoutEvaluationError("Database session required to persist evaluation")
            db_job, persisted_id = self._persist(
                job,
                evaluation,
                create_job_record=create_job_record,
                job_id=job_id,
            )
            evaluation.job_id = db_job.id if db_job else job_id

        return ScoutPipelineResult(
            evaluation=evaluation,
            job=db_job,
            persisted_evaluation_id=persisted_id,
            should_present=should_present_to_user(evaluation),
        )

    def evaluate_from_paths(
        self,
        job_path: str,
        profile_path: str | None = None,
        *,
        persist: bool = False,
    ) -> ScoutPipelineResult:
        path = profile_path or self.settings.candidate_profile_path
        try:
            candidate = load_candidate_profile(path)
        except CandidateProfileError as exc:
            raise ScoutEvaluationError(str(exc)) from exc

        try:
            job = NormalizedJob.model_validate_json(
                open(job_path, encoding="utf-8").read()  # noqa: SIM115
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise ScoutEvaluationError(f"Malformed job input: {exc}") from exc

        return self.evaluate(job, candidate, persist=persist, create_job_record=persist)

    def _persist(
        self,
        job: NormalizedJob,
        evaluation: ScoutEvaluation,
        *,
        create_job_record: bool,
        job_id: int | None,
    ) -> tuple[Job | None, int | None]:
        assert self.session is not None
        job_service = JobService(self.session)
        eval_service = ScoutEvaluationService(self.session)

        db_job: Job | None = None
        if job_id is not None:
            db_job = job_service.require_job(job_id)
        elif create_job_record:
            # Always create as DISCOVERED — never APPROVED
            db_job = job_service.create_job(
                company=job.company,
                title=job.title,
                source=job.source or "scout",
                external_id=job.external_id,
                location=job.location,
                remote_status=job.remote_status,
                salary_min=job.salary_min,
                salary_max=job.salary_max,
                job_url=job.source_url,
                description=job.description,
                fit_score=evaluation.qualification_score / 100.0,
                recommendation_reason=evaluation.summary_reason(),
                status=JobStatus.DISCOVERED,
            )
            # Advance through scoring states when recommended for human review
            db_job.transition_to(JobStatus.SCORED)
            if should_present_to_user(evaluation):
                db_job.transition_to(JobStatus.RECOMMENDED)
                db_job.transition_to(JobStatus.AWAITING_APPROVAL)
            elif evaluation.recommendation == Recommendation.HARD_REJECT:
                db_job.transition_to(JobStatus.ARCHIVED)
            else:
                db_job.transition_to(JobStatus.ARCHIVED)
            db_job.updated_at = datetime.now(timezone.utc)
            self.session.flush()

        if db_job is None:
            return None, None

        evaluation.job_id = db_job.id
        record = eval_service.save_evaluation(db_job.id, evaluation)
        self.session.flush()
        return db_job, record.id

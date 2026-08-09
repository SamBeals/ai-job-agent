"""Persistence helpers for ScoutEvaluation records."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.scout_evaluation import ScoutEvaluationRecord
from app.schemas.evaluation import ScoutEvaluation


class ScoutEvaluationService:
    """Save and query Scout evaluations. Does not approve jobs."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def save_evaluation(self, job_id: int, evaluation: ScoutEvaluation) -> ScoutEvaluationRecord:
        payload = evaluation.model_dump(mode="json")
        record = ScoutEvaluationRecord(
            job_id=job_id,
            qualification_score=float(evaluation.qualification_score),
            desirability_score=float(evaluation.desirability_score),
            recommendation=evaluation.recommendation.value,
            confidence=evaluation.confidence.value,
            evaluation_json=payload,
            evaluator_version=evaluation.evaluator_version,
            evaluator_provider=evaluation.evaluator_provider,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def list_for_job(self, job_id: int) -> list[ScoutEvaluationRecord]:
        stmt = (
            select(ScoutEvaluationRecord)
            .where(ScoutEvaluationRecord.job_id == job_id)
            .order_by(ScoutEvaluationRecord.created_at.desc(), ScoutEvaluationRecord.id.desc())
        )
        return list(self.session.scalars(stmt).all())

    def latest_for_job(self, job_id: int) -> ScoutEvaluationRecord | None:
        rows = self.list_for_job(job_id)
        return rows[0] if rows else None

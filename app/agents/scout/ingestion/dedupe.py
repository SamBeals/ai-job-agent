"""Duplicate job detection for manual re-ingestion."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.scout.ingestion.url_safety import canonicalize_url
from app.models.job import Job
from app.schemas.job_posting import NormalizedJob


def find_duplicate_job(session: Session, job: NormalizedJob) -> Job | None:
    """Find an existing Job for re-evaluation.

    Match priority:
    1. canonicalized source URL / job_url
    2. external_id + source
    3. company + title + location (only when no URL)

    On match, callers should reuse the Job and append a new ScoutEvaluation.
    """
    if job.source_url:
        try:
            canon = canonicalize_url(job.source_url)
        except Exception:  # noqa: BLE001
            canon = job.source_url.rstrip("/")
        # Compare loosely against stored URLs
        candidates = session.scalars(
            select(Job).where(Job.job_url.is_not(None)).order_by(Job.id.desc()).limit(200)
        ).all()
        for existing in candidates:
            if not existing.job_url:
                continue
            try:
                if canonicalize_url(existing.job_url) == canon:
                    return existing
            except Exception:  # noqa: BLE001
                if existing.job_url.rstrip("/") == job.source_url.rstrip("/"):
                    return existing

    if job.external_id:
        existing = session.scalars(
            select(Job).where(
                Job.external_id == job.external_id,
                Job.source == (job.source or "manual"),
            )
        ).first()
        if existing:
            return existing

    if not job.source_url:
        stmt = select(Job).where(
            Job.company == job.company,
            Job.title == job.title,
        )
        if job.location:
            stmt = stmt.where(Job.location == job.location)
        return session.scalars(stmt.order_by(Job.id.desc())).first()

    return None

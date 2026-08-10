"""Shared pytest fixtures — isolated in-memory SQLite per test."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.database.database import Base, create_db_engine
from app.models.job import Job, JobStatus
from app.services.approval_service import ApprovalService
from app.services.job_service import JobService


@pytest.fixture()
def session() -> Session:
    # Ensure Discovery ORM tables register on Base.metadata
    import app.models.discovery  # noqa: F401
    import app.models.work_item  # noqa: F401

    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def job_service(session: Session) -> JobService:
    return JobService(session)


@pytest.fixture()
def approval_service(session: Session) -> ApprovalService:
    return ApprovalService(session)


def make_job(
    job_service: JobService,
    *,
    status: JobStatus = JobStatus.AWAITING_APPROVAL,
    company: str = "Example Corp",
    title: str = "Senior Software Engineer",
) -> Job:
    return job_service.create_job(
        company=company,
        title=title,
        source="test",
        location="Remote",
        remote_status="Remote",
        salary_min=150000,
        salary_max=180000,
        job_url="https://example.com/jobs/test",
        fit_score=0.91,
        recommendation_reason="Strong matches: Java, Spring Boot",
        status=status,
    )

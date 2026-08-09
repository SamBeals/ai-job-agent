"""AI Job Agent — FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.database import get_session, init_db
from app.models.job import JobStatus
from app.services.approval_service import ApprovalService
from app.services.job_service import JobService


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="AI Job Agent",
    description="Agentic job-application system — Phase 1 control plane",
    version="0.1.0",
    lifespan=lifespan,
)


class StatusResponse(BaseModel):
    status: str
    environment: str
    phase: str = "1"
    message: str


class JobSummary(BaseModel):
    id: int
    company: str
    title: str
    status: str
    fit_score: float | None = None
    job_url: str | None = None


class AuthorizationCheck(BaseModel):
    job_id: int
    can_enter_application_pipeline: bool


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status", response_model=StatusResponse)
def status() -> StatusResponse:
    settings = get_settings()
    return StatusResponse(
        status="online",
        environment=settings.app_env,
        message="Phase 1 foundation: Discord control plane + approval boundary.",
    )


@app.get("/jobs/awaiting-approval", response_model=list[JobSummary])
def list_awaiting_approval(session: Session = Depends(get_session)) -> list[JobSummary]:
    jobs = JobService(session).list_awaiting_approval()
    return [
        JobSummary(
            id=j.id,
            company=j.company,
            title=j.title,
            status=j.status,
            fit_score=j.fit_score,
            job_url=j.job_url,
        )
        for j in jobs
    ]


@app.get("/jobs/{job_id}/authorization", response_model=AuthorizationCheck)
def check_authorization(
    job_id: int,
    session: Session = Depends(get_session),
) -> AuthorizationCheck:
    allowed = ApprovalService(session).can_enter_application_pipeline(job_id)
    return AuthorizationCheck(job_id=job_id, can_enter_application_pipeline=allowed)


class TransitionRequest(BaseModel):
    target_status: JobStatus = Field(..., description="Target job status")


@app.post("/jobs/{job_id}/transition", response_model=JobSummary)
def transition_job(
    job_id: int,
    body: TransitionRequest,
    session: Session = Depends(get_session),
) -> JobSummary:
    """Non-approval transitions only. APPROVED must go through ApprovalService."""
    try:
        job = JobService(session).transition(job_id, body.target_status)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:  # InvalidTransitionError, JobNotFoundError
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return JobSummary(
        id=job.id,
        company=job.company,
        title=job.title,
        status=job.status,
        fit_score=job.fit_score,
        job_url=job.job_url,
    )


def run_api() -> None:
    """Run the FastAPI server with uvicorn."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.is_development,
    )


if __name__ == "__main__":
    run_api()

"""Regression: worker must not touch detached ORM after claim Session closes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import DetachedInstanceError

from app.agents.discovery.agent import DiscoveryAgentError, queue_discovery_run
from app.config import Settings, get_settings
from app.database.database import Base, create_db_engine
from app.models.discovery import DiscoveryResult, DiscoveryRun
from app.models.job import JobStatus
from app.models.work_item import AgentWorkItem
from app.schemas.agents import AgentType, WorkItemStatus
from app.schemas.discovery import DiscoveryRunStatus
from app.services.approval_service import ApprovalService
from app.services.job_service import JobService
from app.services.pipeline_orchestrator import PipelineOrchestrator
from app.services.work_item_service import WorkItemService
from app.workers.agent_worker import _capture_claimed_work, process_one
from app.workers.recover_stale_work import recover_stale_running
from app.agents.discovery.providers.fake import FakeDiscoveryProvider
from tests.conftest import make_job
from tests.test_orchestration import PROFILE, _seed_scout_evaluation

ROOT = Path(__file__).resolve().parents[1]
OFFICE_PROFILE = ROOT / "data" / "fixtures" / "profiles" / "test_office_backend_prefs.json"


@pytest.fixture()
def worker_env(monkeypatch):
    """Isolate process_one on an in-memory DB with Fake Discovery providers."""
    import app.models.discovery  # noqa: F401
    import app.models.work_item  # noqa: F401

    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    active_sessions = {"count": 0}
    search_saw_open_session = {"value": False}

    class TrackingSession:
        def __init__(self):
            self._session = factory()

        def __enter__(self):
            active_sessions["count"] += 1
            return self._session.__enter__()

        def __exit__(self, *args):
            active_sessions["count"] -= 1
            return self._session.__exit__(*args)

    def session_factory():
        return TrackingSession()

    monkeypatch.setattr("app.workers.agent_worker.SessionLocal", session_factory)

    settings = Settings(
        candidate_profile_path=str(OFFICE_PROFILE),
        discovery_provider="fake",
        discovery_max_raw_results=100,
        discovery_max_surfaced_results=10,
        llm_provider="mock",
        discord_agent_webhook_url="",
        agent_max_attempts=3,
    )
    monkeypatch.setattr("app.workers.agent_worker.get_settings", lambda: settings)

    class TrackingFake(FakeDiscoveryProvider):
        def search(self, query):  # noqa: ANN001
            if active_sessions["count"] > 0:
                search_saw_open_session["value"] = True
            return super().search(query)

    monkeypatch.setattr(
        "app.agents.discovery.factory.build_discovery_providers",
        lambda *a, **k: [TrackingFake()],
    )
    monkeypatch.setattr(
        "app.workers.agent_worker.build_notification_service",
        lambda *a, **k: MagicMock(),
    )

    get_settings.cache_clear()
    yield {
        "factory": factory,
        "engine": engine,
        "settings": settings,
        "search_saw_open_session": search_saw_open_session,
    }
    get_settings.cache_clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_claimed_work_primitives_safe_after_session_close(worker_env):
    """Exact failure mode: after claim Session closes, ORM access breaks; IDs do not."""
    factory = worker_env["factory"]
    settings = worker_env["settings"]

    with factory() as session:
        run, item = queue_discovery_run(session, settings=settings)
        session.commit()
        work_item_id = item.id
        run_id = run.id

    with factory() as session:
        claimed = WorkItemService(session).claim_next(
            worker_id="w",
            agent_types=[AgentType.DISCOVERY],
        )
        assert claimed is not None
        identity = _capture_claimed_work(claimed)
        session.commit()
    # Session closed — expired detached instance must not be used
    with pytest.raises(DetachedInstanceError):
        _ = claimed.status

    assert identity.work_item_id == work_item_id
    assert identity.discovery_run_id == run_id
    assert identity.agent_type == AgentType.DISCOVERY.value
    assert identity.task_type == "SEARCH_JOBS"


def test_discovery_process_one_end_to_end_no_detached_error(worker_env):
    factory = worker_env["factory"]
    settings = worker_env["settings"]

    with factory() as session:
        queue_discovery_run(session, settings=settings)
        session.commit()

    assert process_one(worker_id="e2e-discovery", max_attempts=3) is True

    with factory() as session:
        item = session.scalars(
            select(AgentWorkItem).where(
                AgentWorkItem.agent_type == AgentType.DISCOVERY.value
            )
        ).first()
        assert item is not None
        assert item.status == WorkItemStatus.COMPLETED.value
        run = session.get(DiscoveryRun, item.discovery_run_id)
        assert run is not None
        assert run.status == DiscoveryRunStatus.COMPLETED.value
        assert run.surfaced_result_count >= 1
        assert session.scalars(select(DiscoveryResult)).first() is not None

    assert worker_env["search_saw_open_session"]["value"] is False


def test_provider_search_outside_claim_transaction(worker_env):
    """Provider network execution must not run inside an open worker Session."""
    factory = worker_env["factory"]
    settings = worker_env["settings"]

    with factory() as session:
        queue_discovery_run(session, settings=settings)
        session.commit()

    process_one(worker_id="net-outside", max_attempts=3)
    assert worker_env["search_saw_open_session"]["value"] is False


def test_resume_process_one_survives_claim_session_close(worker_env, monkeypatch):
    factory = worker_env["factory"]
    settings = worker_env["settings"]
    object.__setattr__(settings, "candidate_profile_path", str(PROFILE))

    with factory() as session:
        jobs = JobService(session)
        approvals = ApprovalService(session)
        job = make_job(jobs, status=JobStatus.AWAITING_APPROVAL)
        _seed_scout_evaluation(session, job.id)
        approvals.approve_job(job.id, approved_by="Sam (test)")
        PipelineOrchestrator(session, notifications=MagicMock()).on_job_preparation_approved(
            job.id
        )
        session.commit()
        job_id = job.id

    assert process_one(worker_id="e2e-resume", max_attempts=3) is True

    with factory() as session:
        item = session.scalars(
            select(AgentWorkItem).where(
                AgentWorkItem.agent_type == AgentType.RESUME.value
            )
        ).first()
        assert item is not None
        assert item.status == WorkItemStatus.COMPLETED.value
        assert item.job_id == job_id


def test_stale_running_discovery_can_be_recovered(worker_env):
    factory = worker_env["factory"]
    settings = worker_env["settings"]

    with factory() as session:
        _run, item = queue_discovery_run(session, settings=settings)
        session.commit()
        claimed = WorkItemService(session).claim_next(
            worker_id="crashed", agent_types=[AgentType.DISCOVERY]
        )
        assert claimed is not None
        claimed.started_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        claimed.heartbeat_at = claimed.started_at
        run = session.get(DiscoveryRun, claimed.discovery_run_id)
        assert run is not None
        run.status = DiscoveryRunStatus.RUNNING.value
        session.commit()
        work_item_id = claimed.id

    with factory() as session:
        actions = recover_stale_running(
            session,
            older_than=timedelta(minutes=5),
            dry_run=False,
        )
        session.commit()
        assert any(a.work_item_id == work_item_id for a in actions)
        item = session.get(AgentWorkItem, work_item_id)
        assert item is not None
        assert item.status == WorkItemStatus.PENDING.value
        assert item.claimed_by is None

    with factory() as session:
        with pytest.raises(DiscoveryAgentError, match="Active Discovery"):
            queue_discovery_run(session, settings=settings)

    assert process_one(worker_id="after-recover", max_attempts=3) is True


def test_fresh_running_work_not_auto_recovered(worker_env):
    factory = worker_env["factory"]
    settings = worker_env["settings"]

    with factory() as session:
        queue_discovery_run(session, settings=settings)
        claimed = WorkItemService(session).claim_next(
            worker_id="alive", agent_types=[AgentType.DISCOVERY]
        )
        assert claimed is not None
        claimed.started_at = datetime.now(timezone.utc)
        claimed.heartbeat_at = claimed.started_at
        session.commit()
        work_item_id = claimed.id

    with factory() as session:
        actions = recover_stale_running(
            session,
            older_than=timedelta(minutes=5),
            dry_run=False,
        )
        assert not any(a.work_item_id == work_item_id for a in actions)
        item = session.get(AgentWorkItem, work_item_id)
        assert item is not None
        assert item.status == WorkItemStatus.RUNNING.value


def test_collapse_duplicate_pending_discovery_queues(worker_env):
    factory = worker_env["factory"]
    settings = worker_env["settings"]

    with factory() as session:
        # Bypass guard to simulate pre-fix duplicate queues
        from app.models.discovery import DiscoveryRun
        from app.schemas.agents import WorkItemTaskType

        for _ in range(3):
            run = DiscoveryRun(status=DiscoveryRunStatus.QUEUED.value)
            session.add(run)
            session.flush()
            session.add(
                AgentWorkItem(
                    discovery_run_id=run.id,
                    agent_type=AgentType.DISCOVERY.value,
                    task_type=WorkItemTaskType.SEARCH_JOBS.value,
                    status=WorkItemStatus.PENDING.value,
                )
            )
            session.flush()
            run.work_item_id = session.scalars(
                select(AgentWorkItem).order_by(AgentWorkItem.id.desc())
            ).first().id
        session.commit()

    with factory() as session:
        actions = recover_stale_running(
            session,
            older_than=timedelta(minutes=5),
            dry_run=False,
        )
        session.commit()
        assert any("duplicate" in a.detail for a in actions)
        pending = list(
            session.scalars(
                select(AgentWorkItem).where(
                    AgentWorkItem.agent_type == AgentType.DISCOVERY.value,
                    AgentWorkItem.status == WorkItemStatus.PENDING.value,
                )
            ).all()
        )
        assert len(pending) == 1

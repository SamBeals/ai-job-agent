"""Phase 3.2 Discovery Agent — fake provider only (no live network)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.agents.discovery.agent import DiscoveryAgent, queue_discovery_run
from app.agents.discovery.dedupe import dedupe_within_run
from app.agents.discovery.filters import prefilter_candidate
from app.agents.discovery.providers.fake import FakeDiscoveryProvider
from app.agents.discovery.queries import plan_discovery_query
from app.agents.discovery.ranking import score_candidate
from app.agents.discovery.scout_bridge import dismiss_discovery_result, scout_discovery_result
from app.agents.scout.profile_loader import load_candidate_profile
from app.config import Settings
from app.discord.discovery_views import DiscoveryResultView, discovery_result_embed
from app.discord.pipeline_embeds import agents_status_embed
from app.models.approval import Approval
from app.models.discovery import DiscoveryResult, DiscoveryRun
from app.models.pipeline import ApplicationPipeline
from app.models.submission_authorization import SubmissionAuthorization
from app.models.work_item import AgentWorkItem
from app.schemas.agents import AgentType, WorkItemStatus, WorkItemTaskType
from app.schemas.discovery import (
    DiscoveryResultStatus,
    DiscoveryRunStatus,
    RankedDiscoveryCandidate,
    RawDiscoveryResult,
)
from app.services.approval_service import ApprovalService
from app.services.notifications import RecordingNotificationService
from app.services.pipeline_orchestrator import PipelineOrchestrator
from app.services.work_item_service import WorkItemService


PROFILE = "data/fixtures/profiles/test_office_backend_prefs.json"


def _settings(**overrides) -> Settings:
    base = dict(
        candidate_profile_path=PROFILE,
        discovery_provider="fake",
        discovery_max_raw_results=100,
        discovery_max_surfaced_results=10,
        llm_provider="mock",
        discord_agent_webhook_url="",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture()
def profile():
    return load_candidate_profile(PROFILE)


def test_queue_discover_creates_work_item_without_searching(session: Session, monkeypatch):
    """ /discover creates DiscoveryRun + work item; does not call providers. """
    called = {"search": 0}

    class Boom(FakeDiscoveryProvider):
        def search(self, query):  # noqa: ANN001
            called["search"] += 1
            return super().search(query)

    monkeypatch.setattr(
        "app.agents.discovery.factory.build_discovery_providers",
        lambda *a, **k: [Boom()],
    )
    settings = _settings()
    run, item = queue_discovery_run(session, settings=settings)
    session.commit()
    assert run.id is not None
    assert item.agent_type == AgentType.DISCOVERY.value
    assert item.task_type == WorkItemTaskType.SEARCH_JOBS.value
    assert item.status == WorkItemStatus.PENDING.value
    assert item.discovery_run_id == run.id
    assert item.job_id is None
    assert item.pipeline_id is None
    assert called["search"] == 0


def test_worker_claims_discovery_and_running_notification(session: Session, monkeypatch):
    settings = _settings()
    notifications = RecordingNotificationService()
    run, item = queue_discovery_run(session, settings=settings)
    session.commit()
    item_id = item.id

    # Claim + started notify
    work = WorkItemService(session)
    claimed = work.claim_next(
        worker_id="test-worker",
        agent_types=[AgentType.DISCOVERY],
    )
    assert claimed is not None
    assert claimed.id == item_id
    assert claimed.status == WorkItemStatus.RUNNING.value
    orch = PipelineOrchestrator(session, notifications=notifications)
    orch.on_work_item_started(claimed.id)
    session.commit()
    started = [e for e in notifications.events if e.kind == "work_item_started"]
    assert len(started) == 1
    assert started[0].agent_type == AgentType.DISCOVERY.value
    assert started[0].metadata.get("status") == "RUNNING"
    assert claimed.status == WorkItemStatus.RUNNING.value


def test_fake_provider_filter_rank_dedupe(session: Session, profile):
    settings = _settings()
    agent = DiscoveryAgent(
        session,
        settings=settings,
        providers=[FakeDiscoveryProvider()],
    )
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    session.commit()

    assert result.run.raw_result_count == 8
    # low salary, frontend-only, helpdesk filtered; duplicate URL removed
    assert result.run.filtered_result_count >= 3
    assert result.run.surfaced_result_count >= 3
    assert result.run.status == DiscoveryRunStatus.COMPLETED.value

    titles = {r.title for r in result.surfaced}
    companies = {r.company for r in result.surfaced}
    assert "Help Desk Technician" not in titles
    assert "Budget Soft" not in companies  # below min salary
    assert all(r.open_url for r in result.surfaced)
    assert all(r.provider for r in result.surfaced)

    # Chandler backend should outrank remote when both present
    by_company = {r.company: r for r in result.surfaced}
    if "Desert Systems" in by_company and "Cloud Harbor" in by_company:
        assert by_company["Desert Systems"].discovery_score >= by_company["Cloud Harbor"].discovery_score


def test_unknown_salary_not_filtered(profile):
    raw = RawDiscoveryResult(
        provider="t",
        source_name="t",
        external_id="1",
        title="Software Engineer",
        company="X",
        location_text="Mesa, AZ",
        work_arrangement="onsite",
        salary_min=None,
        salary_max=None,
        job_url="https://example.com/a",
        canonical_url="https://example.com/a",
    )
    cand = prefilter_candidate(profile, raw)
    assert cand.filtered is False


def test_explicit_salary_below_minimum_filtered(profile):
    raw = RawDiscoveryResult(
        provider="t",
        source_name="t",
        external_id="2",
        title="Backend Engineer",
        company="Y",
        location_text="Tempe, AZ",
        work_arrangement="hybrid",
        salary_min=70000,
        salary_max=90000,
        salary_period="year",
        job_url="https://example.com/b",
        canonical_url="https://example.com/b",
    )
    cand = prefilter_candidate(profile, raw)
    assert cand.filtered is True
    assert cand.filter_reason == "SALARY_BELOW_MINIMUM"


def test_unrelated_role_filtered(profile):
    raw = RawDiscoveryResult(
        provider="t",
        source_name="t",
        external_id="3",
        title="Help Desk Technician",
        company="Z",
        location_text="Phoenix, AZ",
        work_arrangement="onsite",
        salary_min=120000,
        salary_max=130000,
        salary_period="year",
        job_url="https://example.com/c",
        canonical_url="https://example.com/c",
    )
    assert prefilter_candidate(profile, raw).filtered is True


def test_frontend_only_filtered_when_prefers_backend(profile):
    raw = RawDiscoveryResult(
        provider="t",
        source_name="t",
        external_id="4",
        title="Frontend Engineer",
        company="Pix",
        location_text="Scottsdale, AZ",
        work_arrangement="hybrid",
        salary_min=130000,
        salary_max=150000,
        salary_period="year",
        description_snippet="React only",
        job_url="https://example.com/d",
        canonical_url="https://example.com/d",
    )
    assert prefilter_candidate(profile, raw).filtered is True


def test_remote_acceptable_and_chandler_outranks(profile):
    remote = RawDiscoveryResult(
        provider="t",
        source_name="t",
        external_id="r",
        title="Backend Engineer",
        company="RemoteCo",
        location_text="Remote - US",
        work_arrangement="remote",
        salary_min=130000,
        salary_max=160000,
        salary_period="year",
        description_snippet="Java backend",
        job_url="https://example.com/r",
        canonical_url="https://example.com/r",
    )
    chandler = RawDiscoveryResult(
        provider="t",
        source_name="t",
        external_id="c",
        title="Backend Software Engineer",
        company="LocalCo",
        location_text="Chandler, AZ",
        work_arrangement="hybrid",
        salary_min=125000,
        salary_max=155000,
        salary_period="year",
        description_snippet="Java backend",
        job_url="https://example.com/c2",
        canonical_url="https://example.com/c2",
    )
    assert prefilter_candidate(profile, remote).filtered is False
    assert prefilter_candidate(profile, chandler).filtered is False
    scored_r = score_candidate(profile, RankedDiscoveryCandidate(raw=remote))
    scored_c = score_candidate(profile, RankedDiscoveryCandidate(raw=chandler))
    assert "REMOTE_ACCEPTABLE" in scored_r.reason_codes
    assert "CHANDLER" in scored_c.reason_codes
    assert scored_c.discovery_score > scored_r.discovery_score


def test_dedupe_url_provider_and_identity():
    a = RankedDiscoveryCandidate(
        raw=RawDiscoveryResult(
            provider="p",
            source_name="p",
            external_id="1",
            title="Backend Software Engineer",
            company="Desert Systems",
            location_text="Chandler, AZ",
            job_url="https://example.com/jobs/x",
            canonical_url="https://example.com/jobs/x",
        ),
        discovery_score=90,
    )
    b = RankedDiscoveryCandidate(
        raw=RawDiscoveryResult(
            provider="p",
            source_name="p",
            external_id="2",
            title="Backend Software Engineer",
            company="Desert Systems",
            location_text="Chandler, AZ",
            job_url="https://example.com/jobs/x",
            canonical_url="https://example.com/jobs/x",
        ),
        discovery_score=80,
    )
    c = RankedDiscoveryCandidate(
        raw=RawDiscoveryResult(
            provider="other",
            source_name="other",
            external_id="1",
            title="Backend Software Engineer",
            company="Desert Systems",
            location_text="Chandler, AZ",
            job_url="https://example.com/jobs/y",
            canonical_url="https://example.com/jobs/y",
        ),
        discovery_score=70,
    )
    # same company/title/location as a — identity duplicate even with different URL
    d = RankedDiscoveryCandidate(
        raw=RawDiscoveryResult(
            provider="q",
            source_name="q",
            external_id="9",
            title="Backend Software Engineer",
            company="Desert Systems",
            location_text="Chandler, AZ",
            job_url="https://example.com/jobs/z",
            canonical_url="https://example.com/jobs/z",
        ),
        discovery_score=60,
    )
    out = dedupe_within_run([a, b, c, d])
    assert len(out) == 1
    assert out[0].raw.external_id == "1"


def test_cross_run_dismissed_and_scouted_do_not_resurface(session: Session, profile):
    settings = _settings()
    agent = DiscoveryAgent(session, settings=settings, providers=[FakeDiscoveryProvider()])
    run1 = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run1)
    session.flush()
    first = agent.execute_run(run1)
    session.commit()
    assert first.surfaced
    target = first.surfaced[0]
    dismiss_discovery_result(session, target.id)
    session.commit()

    run2 = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run2)
    session.flush()
    second = agent.execute_run(run2)
    session.commit()
    ids = {r.external_id for r in second.surfaced}
    assert target.external_id not in ids

    # Scouted also blocked
    if second.surfaced:
        s = second.surfaced[0]
        s.status = DiscoveryResultStatus.SCOUTED.value
        session.commit()
        run3 = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
        session.add(run3)
        session.flush()
        third = agent.execute_run(run3)
        session.commit()
        assert s.external_id not in {r.external_id for r in third.surfaced}


def test_partial_and_failed_runs(session: Session):
    settings = _settings()

    class Ok:
        name = "ok"

        def search(self, query):  # noqa: ANN001
            return [
                RawDiscoveryResult(
                    provider="ok",
                    source_name="ok",
                    external_id="ok1",
                    title="Backend Software Engineer",
                    company="Good Co",
                    location_text="Chandler, AZ",
                    work_arrangement="hybrid",
                    salary_min=120000,
                    salary_max=140000,
                    salary_period="year",
                    description_snippet="Java backend services",
                    description_full="Java backend services " * 40,
                    job_url="https://example.com/ok1",
                    canonical_url="https://example.com/ok1",
                )
            ]

    class Boom:
        name = "boom"

        def search(self, query):  # noqa: ANN001
            raise RuntimeError("provider down")

    agent = DiscoveryAgent(session, settings=settings, providers=[Boom(), Ok()])
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    session.commit()
    assert result.run.status == DiscoveryRunStatus.PARTIAL.value
    assert result.run.surfaced_result_count >= 1

    agent_fail = DiscoveryAgent(session, settings=settings, providers=[Boom()])
    run_f = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run_f)
    session.flush()
    failed = agent_fail.execute_run(run_f)
    session.commit()
    assert failed.run.status == DiscoveryRunStatus.FAILED.value
    assert failed.run.surfaced_result_count == 0
    assert failed.surfaced == []


def test_discovery_cannot_authorize(session: Session):
    settings = _settings()
    agent = DiscoveryAgent(session, settings=settings, providers=[FakeDiscoveryProvider()])
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    session.commit()
    # Perfect score still no approval / pipeline / submission
    for row in result.surfaced:
        row.discovery_score = 100
    session.commit()

    assert session.query(Approval).count() == 0
    assert session.query(ApplicationPipeline).count() == 0
    assert session.query(SubmissionAuthorization).count() == 0
    resume_items = [
        i
        for i in session.query(AgentWorkItem).all()
        if i.agent_type == AgentType.RESUME.value
    ]
    assert resume_items == []
    approvals = ApprovalService(session)
    for row in result.surfaced:
        if row.job_id:
            assert approvals.can_prepare_application(row.job_id) is False


def test_view_job_url_and_dismiss(session: Session):
    settings = _settings()
    agent = DiscoveryAgent(session, settings=settings, providers=[FakeDiscoveryProvider()])
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    session.commit()
    row = result.surfaced[0]
    embed = discovery_result_embed(row)
    assert embed.url == row.open_url
    view = DiscoveryResultView(row.id, row.open_url)
    link_buttons = [i for i in view.children if getattr(i, "url", None)]
    assert link_buttons and link_buttons[0].url == row.open_url

    dismissed = dismiss_discovery_result(session, row.id)
    session.commit()
    assert dismissed.status == DiscoveryResultStatus.DISMISSED.value


def test_scout_this_uses_structured_content_and_existing_pipeline(
    session: Session, monkeypatch
):
    settings = _settings()
    agent = DiscoveryAgent(session, settings=settings, providers=[FakeDiscoveryProvider()])
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    session.commit()
    # Prefer the Chandler job which has description_full
    row = next(r for r in result.surfaced if r.external_id == "fake-chandler-backend")

    # Force URL ingest to fail so structured path is required
    from app.agents.scout.ingestion.models import IngestionError

    def _boom(self, url, **kwargs):  # noqa: ANN001
        raise IngestionError("blocked", code="FETCH_BLOCKED")

    monkeypatch.setattr(
        "app.agents.scout.ingestion.service.JobIngestionService.ingest_url",
        _boom,
    )

    outcome = scout_discovery_result(session, row.id, settings=settings)
    session.commit()
    assert outcome.ok is True
    assert outcome.used_structured_content is True
    assert outcome.job is not None
    assert outcome.evaluation is not None
    assert session.get(DiscoveryResult, row.id).status == DiscoveryResultStatus.SCOUTED.value
    assert session.get(DiscoveryResult, row.id).job_id == outcome.job.id

    # Still no authorization
    assert ApprovalService(session).can_prepare_application(outcome.job.id) is False
    assert session.query(ApplicationPipeline).count() == 0
    assert session.query(SubmissionAuthorization).count() == 0


def test_scout_insufficient_content_paste_fallback(session: Session, monkeypatch):
    settings = _settings()
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    row = DiscoveryResult(
        discovery_run_id=run.id,
        provider="t",
        external_id="thin",
        source_name="t",
        title="Backend Engineer",
        company="Thin",
        location="Chandler, AZ",
        work_arrangement="hybrid",
        job_url="https://example.com/thin",
        canonical_url="https://example.com/thin",
        description_snippet="Short",
        description_full="Short",
        discovery_score=50,
        status=DiscoveryResultStatus.SURFACED.value,
    )
    session.add(row)
    session.commit()

    from app.agents.scout.ingestion.models import IngestionError

    monkeypatch.setattr(
        "app.agents.scout.ingestion.service.JobIngestionService.ingest_url",
        lambda *a, **k: (_ for _ in ()).throw(IngestionError("blocked", code="FETCH_BLOCKED")),
    )
    outcome = scout_discovery_result(session, row.id, settings=settings)
    assert outcome.ok is False
    assert outcome.needs_paste_fallback is True
    assert "PASTE JOB" in outcome.message


def test_webhook_failure_does_not_corrupt_run(session: Session):
    settings = _settings()

    class Exploding:
        def notify(self, event):  # noqa: ANN001
            raise RuntimeError("webhook down")

    agent = DiscoveryAgent(
        session,
        settings=settings,
        notifications=Exploding(),
        providers=[FakeDiscoveryProvider()],
    )
    run, item = queue_discovery_run(session, settings=settings)
    session.commit()
    work = WorkItemService(session)
    claimed = work.claim_next(worker_id="w", agent_types=[AgentType.DISCOVERY])
    assert claimed is not None
    orch = PipelineOrchestrator(session, notifications=Exploding())
    orch.on_work_item_started(claimed.id)  # must not raise
    result = agent.process_work_item(claimed)
    session.commit()
    assert result.run.status == DiscoveryRunStatus.COMPLETED.value
    orch.on_discovery_completed(claimed.id, result.run.id)  # swallowed
    session.refresh(result.run)
    assert result.run.status == DiscoveryRunStatus.COMPLETED.value


def test_agents_embed_reports_discovery_implemented():
    embed = agents_status_embed(
        {"PENDING": 0, "RUNNING": 0, "COMPLETED": 2},
        discovery_counts={"PENDING": 1, "RUNNING": 0, "COMPLETED": 4},
    )
    assert "Discovery Agent" in embed.description
    assert "NOT IMPLEMENTED" not in embed.description.split("Discovery Agent")[1].split("\n")[0]
    assert "4 completed" in embed.description


def test_end_to_end_discover_to_scout_no_authorization(session: Session, monkeypatch):
    """Full path: queue → claim → fake provider → surface → SCOUT THIS → no Gate 1."""
    settings = _settings()
    notifications = RecordingNotificationService()

    run, item = queue_discovery_run(session, settings=settings)
    session.commit()

    work = WorkItemService(session)
    claimed = work.claim_next(worker_id="e2e", agent_types=[AgentType.DISCOVERY])
    orch = PipelineOrchestrator(session, notifications=notifications)
    orch.on_work_item_started(claimed.id)
    agent = DiscoveryAgent(
        session,
        settings=settings,
        notifications=notifications,
        providers=[FakeDiscoveryProvider()],
    )
    exec_result = agent.process_work_item(claimed)
    session.commit()
    orch.on_discovery_completed(claimed.id, exec_result.run.id)
    session.commit()

    assert exec_result.run.surfaced_result_count >= 1
    row = next(r for r in exec_result.surfaced if "backend" in r.title.lower())

    outcome = scout_discovery_result(session, row.id, settings=settings)
    session.commit()
    assert outcome.ok
    assert outcome.job is not None

    assert session.query(Approval).count() == 0
    assert session.query(ApplicationPipeline).count() == 0
    assert session.query(SubmissionAuthorization).count() == 0
    assert not any(
        i.agent_type == AgentType.RESUME.value for i in session.query(AgentWorkItem).all()
    )
    assert ApprovalService(session).can_prepare_application(outcome.job.id) is False


def test_plan_query_uses_preferences(profile):
    q = plan_discovery_query(profile, max_raw_results=50)
    assert q.minimum_base_salary == 110000
    assert q.prefers_backend is True
    assert any("Backend" in t or "Software" in t for t in q.role_terms)
    assert q.include_remote is True


def test_exclude_results_without_url(session: Session):
    settings = _settings()

    class NoUrl:
        name = "nourl"

        def search(self, query):  # noqa: ANN001
            return [
                RawDiscoveryResult(
                    provider="nourl",
                    source_name="nourl",
                    external_id="x",
                    title="Backend Software Engineer",
                    company="No URL Co",
                    location_text="Chandler, AZ",
                    work_arrangement="hybrid",
                    salary_min=120000,
                    salary_max=140000,
                    salary_period="year",
                    description_snippet="Java backend",
                    job_url=None,
                    canonical_url=None,
                )
            ]

    agent = DiscoveryAgent(session, settings=settings, providers=[NoUrl()])
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    assert result.run.surfaced_result_count == 0

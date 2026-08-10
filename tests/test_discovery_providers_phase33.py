"""Phase 3.3 Discovery source expansion — fake HTTP / no live network."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy.orm import Session

from app.agents.discovery.agent import DiscoveryAgent
from app.agents.discovery.boards import load_discovery_boards
from app.agents.discovery.dedupe import dedupe_within_run, normalize_discovery_url
from app.agents.discovery.factory import build_discovery_providers
from app.agents.discovery.filters import prefilter_candidate
from app.agents.discovery.providers.adzuna import AdzunaDiscoveryProvider
from app.agents.discovery.providers.ashby import AshbyDiscoveryProvider
from app.agents.discovery.providers.lever import LeverDiscoveryProvider
from app.agents.discovery.providers.muse import MuseDiscoveryProvider
from app.agents.discovery.ranking import score_candidate
from app.agents.discovery.scout_bridge import scout_discovery_result
from app.agents.scout.profile_loader import load_candidate_profile
from app.config import Settings
from app.discord.agent_activity import discovery_completed_embeds
from app.models.approval import Approval
from app.models.discovery import DiscoveryResult, DiscoveryRun
from app.models.pipeline import ApplicationPipeline
from app.models.submission_authorization import SubmissionAuthorization
from app.models.work_item import AgentWorkItem
from app.schemas.agents import AgentType
from app.schemas.discovery import (
    DiscoveryQuery,
    DiscoveryResultStatus,
    DiscoveryRunStatus,
    RankedDiscoveryCandidate,
    RawDiscoveryResult,
)
from app.services.approval_service import ApprovalService


PROFILE = "data/fixtures/profiles/test_office_backend_prefs.json"


@pytest.fixture()
def profile():
    return load_candidate_profile(PROFILE)


def _settings(**overrides) -> Settings:
    base = dict(
        candidate_profile_path=PROFILE,
        discovery_provider="fake",
        discovery_max_raw_results=100,
        discovery_max_surfaced_results=10,
        discovery_min_surface_score=45,
        discovery_boards_path="config/discovery_boards.json",
        llm_provider="mock",
        discord_agent_webhook_url="",
        adzuna_app_id="",
        adzuna_app_key="",
    )
    base.update(overrides)
    return Settings(**base)


def _query(**overrides) -> DiscoveryQuery:
    base = dict(
        role_terms=["Backend Software Engineer", "Software Engineer"],
        location_terms=["Chandler, Arizona", "Phoenix, Arizona"],
        include_remote=True,
        max_raw_results=50,
    )
    base.update(overrides)
    return DiscoveryQuery(**base)


def test_load_discovery_boards_registry():
    boards = load_discovery_boards("config/discovery_boards.json")
    assert any(e.tenant == "godaddy" for e in boards["greenhouse"])
    assert any(e.tenant == "palantir" for e in boards["lever"])
    assert any(e.tenant == "notion" for e in boards["ashby"])


def test_factory_auto_includes_new_providers_without_adzuna_keys(monkeypatch):
    settings = _settings(
        discovery_provider="auto",
        discovery_greenhouse_enabled=True,
        discovery_lever_enabled=True,
        discovery_ashby_enabled=True,
        discovery_muse_enabled=True,
        discovery_adzuna_enabled=True,  # enabled but no keys → skipped
        discovery_remotive_enabled=True,
        discovery_enable_remotive=True,
        discovery_greenhouse_boards="stripe",
        discovery_lever_sites="palantir",
        discovery_ashby_boards="notion",
    )
    # Avoid depending on full registry size — stubs still construct
    providers = build_discovery_providers(settings)
    names = {p.name for p in providers}
    assert "greenhouse" in names
    assert "lever" in names
    assert "ashby" in names
    assert "muse" in names
    assert "remotive" in names
    assert "adzuna" not in names


def test_adzuna_missing_credentials_returns_empty_not_crash():
    provider = AdzunaDiscoveryProvider(app_id="", app_key="")
    assert provider.search(_query()) == []


def test_lever_normalizes_payload(monkeypatch, profile):
    payload = [
        {
            "id": "abc-123",
            "text": "Backend Software Engineer",
            "categories": {"location": "Phoenix, AZ", "team": "Engineering"},
            "workplaceType": "hybrid",
            "descriptionPlain": "Build Java services in Phoenix.",
            "description": "<p>Build Java services</p>",
            "hostedUrl": "https://jobs.lever.co/example/abc-123",
            "applyUrl": "https://jobs.lever.co/example/abc-123/apply",
            "createdAt": 1_700_000_000_000,
            "country": "US",
        }
    ]

    class FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):  # noqa: ANN001
            assert "mode" in (params or {})
            assert "example" in url
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    provider = LeverDiscoveryProvider(
        site_tokens=["example"], company_names={"example": "Example Co"}
    )
    results = provider.search(_query())
    assert len(results) == 1
    row = results[0]
    assert row.provider == "lever"
    assert row.external_id == "example:abc-123"
    assert row.canonical_url == "https://jobs.lever.co/example/abc-123"
    assert "Java" in (row.description_full or "")
    assert row.work_arrangement == "hybrid"

    cand = prefilter_candidate(profile, row)
    assert cand.filtered is False
    scored = score_candidate(profile, cand)
    assert scored.discovery_score >= 45
    assert "LOCAL_HYBRID" in scored.reason_codes or "PHOENIX_METRO" in scored.reason_codes


def test_ashby_normalizes_and_preserves_salary(monkeypatch, profile):
    payload = {
        "jobs": [
            {
                "id": "job-1",
                "title": "Software Engineer",
                "location": "Chandler, AZ",
                "isListed": True,
                "isRemote": False,
                "workplaceType": "Hybrid",
                "descriptionPlain": "Backend Java APIs in Chandler.",
                "jobUrl": "https://jobs.ashbyhq.com/example/job-1",
                "applyUrl": "https://jobs.ashbyhq.com/example/job-1/apply",
                "publishedAt": "2026-08-01T00:00:00+00:00",
                "compensation": {
                    "compensationTiers": [
                        {
                            "components": [
                                {
                                    "compensationType": "Salary",
                                    "minValue": 140000,
                                    "maxValue": 180000,
                                    "currencyCode": "USD",
                                }
                            ]
                        }
                    ]
                },
            }
        ]
    }

    class FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):  # noqa: ANN001
            assert "includeCompensation" in (params or {})
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    provider = AshbyDiscoveryProvider(
        board_tokens=["example"], company_names={"example": "Example"}
    )
    results = provider.search(_query())
    assert len(results) == 1
    row = results[0]
    assert row.external_id == "example:job-1"
    assert row.salary_min == 140000
    assert row.canonical_url.endswith("/job-1")
    assert row.description_full
    scored = score_candidate(profile, prefilter_candidate(profile, row))
    assert scored.discovery_score >= 45
    assert "CHANDLER" in scored.reason_codes


def test_muse_maps_structured_contents(monkeypatch):
    payload = {
        "page": 0,
        "page_count": 1,
        "results": [
            {
                "id": 99,
                "name": "Backend Software Engineer",
                "company": {"name": "Local Bank", "short_name": "localbank"},
                "locations": [{"name": "Chandler, AZ"}],
                "contents": "<p>Java backend services</p>",
                "refs": {"landing_page": "https://www.themuse.com/jobs/localbank/be"},
                "publication_date": "2026-08-01T00:00:00Z",
                "categories": [{"name": "Software Engineering"}],
            }
        ],
    }

    class FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):  # noqa: ANN001
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    results = MuseDiscoveryProvider(max_pages=1).search(_query())
    assert results
    assert results[0].external_id == "99"
    assert results[0].description_full
    assert results[0].canonical_url.startswith("https://www.themuse.com/")


def test_provider_timeout_isolated_partial(session: Session):
    settings = _settings()

    class Ok:
        name = "ok"

        def search(self, query):  # noqa: ANN001
            return [
                RawDiscoveryResult(
                    provider="ok",
                    source_name="ok",
                    external_id="1",
                    title="Backend Software Engineer",
                    company="Good",
                    location_text="Chandler, AZ",
                    work_arrangement="hybrid",
                    salary_min=130000,
                    salary_max=160000,
                    salary_period="year",
                    description_snippet="Java backend",
                    description_full="Java backend " * 40,
                    job_url="https://example.com/ok",
                    canonical_url="https://example.com/ok",
                )
            ]

    class Boom:
        name = "boom"

        def search(self, query):  # noqa: ANN001
            raise httpx.TimeoutException("timeout")

    agent = DiscoveryAgent(session, settings=settings, providers=[Boom(), Ok()])
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    assert result.status == DiscoveryRunStatus.PARTIAL.value
    assert result.surfaced_result_count >= 1


def test_all_providers_fail_failed(session: Session):
    settings = _settings()

    class Boom:
        name = "boom"

        def search(self, query):  # noqa: ANN001
            raise RuntimeError("down")

    agent = DiscoveryAgent(session, settings=settings, providers=[Boom()])
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    assert result.status == DiscoveryRunStatus.FAILED.value
    assert result.surfaced_result_count == 0


def test_foreign_and_salary_filters_still_apply(profile):
    foreign = RawDiscoveryResult(
        provider="lever",
        source_name="lever:x",
        external_id="x:1",
        title="Backend Software Engineer",
        company="Euro",
        location_text="Berlin, Germany",
        work_arrangement="hybrid",
        salary_min=150000,
        salary_max=180000,
        salary_period="year",
        job_url="https://jobs.lever.co/x/1",
        canonical_url="https://jobs.lever.co/x/1",
    )
    low = RawDiscoveryResult(
        provider="ashby",
        source_name="ashby:x",
        external_id="x:2",
        title="Backend Software Engineer",
        company="Cheap",
        location_text="Phoenix, AZ",
        work_arrangement="hybrid",
        salary_min=70000,
        salary_max=90000,
        salary_period="year",
        job_url="https://jobs.ashbyhq.com/x/2",
        canonical_url="https://jobs.ashbyhq.com/x/2",
    )
    assert prefilter_candidate(profile, foreign).filter_reason == "FOREIGN_LOCATION"
    assert prefilter_candidate(profile, low).filter_reason == "SALARY_BELOW_MINIMUM"


def test_cross_provider_url_dedupe_and_distinct_titles():
    shared = "https://example.com/jobs/shared?utm_source=x"
    a = RankedDiscoveryCandidate(
        raw=RawDiscoveryResult(
            provider="greenhouse",
            source_name="gh",
            external_id="gh:1",
            title="Backend Software Engineer",
            company="Acme",
            location_text="Phoenix, AZ",
            job_url=shared,
            canonical_url=shared,
        ),
        discovery_score=80,
    )
    b = RankedDiscoveryCandidate(
        raw=RawDiscoveryResult(
            provider="lever",
            source_name="lv",
            external_id="lv:1",
            title="Backend Software Engineer",
            company="Acme",
            location_text="Phoenix, AZ",
            job_url="https://example.com/jobs/shared",
            canonical_url="https://example.com/jobs/shared",
        ),
        discovery_score=70,
    )
    # Similar title but different real job — must NOT collapse
    c = RankedDiscoveryCandidate(
        raw=RawDiscoveryResult(
            provider="ashby",
            source_name="as",
            external_id="as:1",
            title="Backend Software Engineer",
            company="Other Co",
            location_text="Tempe, AZ",
            job_url="https://example.com/jobs/other",
            canonical_url="https://example.com/jobs/other",
        ),
        discovery_score=75,
    )
    out = dedupe_within_run([a, b, c])
    assert len(out) == 2
    ids = {x.raw.external_id for x in out}
    assert "gh:1" in ids
    assert "as:1" in ids


def test_cross_provider_integration_surfaces_unique(session: Session, profile):
    settings = _settings(discovery_max_surfaced_results=10)

    class Gh:
        name = "greenhouse"

        def search(self, query):  # noqa: ANN001
            return [
                _strong("A", "https://example.com/a", "greenhouse", "gh:a"),
                _strong("B", "https://example.com/b", "greenhouse", "gh:b"),
            ]

    class Lv:
        name = "lever"

        def search(self, query):  # noqa: ANN001
            return [
                _strong("B", "https://example.com/b", "lever", "lv:b"),  # dup URL
                _strong("C", "https://example.com/c", "lever", "lv:c"),
            ]

    class Broad:
        name = "muse"

        def search(self, query):  # noqa: ANN001
            return [
                _strong("C", "https://example.com/c", "muse", "m:c"),  # dup URL
                _strong("D", "https://example.com/d", "muse", "m:d"),
            ]

    agent = DiscoveryAgent(
        session, settings=settings, providers=[Gh(), Lv(), Broad()]
    )
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    session.commit()
    rows = [session.get(DiscoveryResult, i) for i in result.surfaced_ids]
    titles = sorted(r.title for r in rows if r)
    assert titles == [
        "Backend Software Engineer A",
        "Backend Software Engineer B",
        "Backend Software Engineer C",
        "Backend Software Engineer D",
    ]
    assert session.query(Approval).count() == 0
    assert session.query(ApplicationPipeline).count() == 0
    assert session.query(SubmissionAuthorization).count() == 0


def _strong(letter: str, url: str, provider: str, external_id: str) -> RawDiscoveryResult:
    return RawDiscoveryResult(
        provider=provider,
        source_name=provider,
        external_id=external_id,
        title=f"Backend Software Engineer {letter}",
        company="Acme",
        location_text="Chandler, AZ",
        work_arrangement="hybrid",
        salary_min=130000,
        salary_max=160000,
        salary_period="year",
        description_snippet="Backend Java software engineer role",
        description_full="Backend Java software engineer role " * 30,
        job_url=url,
        canonical_url=url,
        published_at=datetime.now(timezone.utc),
    )


def test_completion_message_cases():
    zero_q = discovery_completed_embeds(
        run_id=1,
        work_item_id=1,
        sources_searched=3,
        raw_result_count=10,
        filtered_result_count=2,
        quality_result_count=0,
        previously_seen_count=0,
        surfaced_result_count=0,
    )[0]["description"]
    assert "quality threshold" in zero_q.lower()
    assert "quality beat volume" not in zero_q.lower()

    all_seen = discovery_completed_embeds(
        run_id=1,
        work_item_id=1,
        sources_searched=3,
        raw_result_count=100,
        filtered_result_count=20,
        quality_result_count=9,
        previously_seen_count=9,
        surfaced_result_count=0,
    )[0]["description"]
    assert "already seen" in all_seen.lower()
    assert "Previously seen:** 9" in all_seen
    assert "quality beat volume" not in all_seen.lower()

    fresh = discovery_completed_embeds(
        run_id=1,
        work_item_id=1,
        sources_searched=3,
        raw_result_count=100,
        filtered_result_count=20,
        quality_result_count=6,
        previously_seen_count=0,
        surfaced_result_count=6,
    )[0]["description"]
    assert "6** strong new opportunities found" in fresh


def test_previously_seen_counted(session: Session):
    settings = _settings()
    run1 = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run1)
    session.flush()
    prior = DiscoveryResult(
        discovery_run_id=run1.id,
        provider="lever",
        external_id="ex:1",
        source_name="lever:ex",
        title="Backend Software Engineer",
        company="Acme",
        location="Chandler, AZ",
        work_arrangement="hybrid",
        job_url="https://jobs.lever.co/ex/1",
        canonical_url="https://jobs.lever.co/ex/1",
        description_snippet="Java",
        description_full="Java backend " * 40,
        discovery_score=90,
        status=DiscoveryResultStatus.SURFACED.value,
    )
    session.add(prior)
    session.commit()

    class P:
        name = "lever"

        def search(self, query):  # noqa: ANN001
            return [
                RawDiscoveryResult(
                    provider="lever",
                    source_name="lever:ex",
                    external_id="ex:1",
                    title="Backend Software Engineer",
                    company="Acme",
                    location_text="Chandler, AZ",
                    work_arrangement="hybrid",
                    salary_min=130000,
                    salary_max=160000,
                    salary_period="year",
                    description_snippet="Java backend",
                    description_full="Java backend " * 40,
                    job_url="https://jobs.lever.co/ex/1",
                    canonical_url="https://jobs.lever.co/ex/1",
                )
            ]

    agent = DiscoveryAgent(session, settings=settings, providers=[P()])
    run2 = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run2)
    session.flush()
    result = agent.execute_run(run2)
    session.commit()
    assert result.quality_result_count >= 1
    assert result.previously_seen_count >= 1
    assert result.surfaced_result_count == 0
    run_row = session.get(DiscoveryRun, result.run_id)
    assert run_row.previously_seen_count >= 1


def test_normalize_url_strips_utm_keeps_gh_jid():
    a = normalize_discovery_url("https://Example.com/jobs/1?utm_source=x&utm_medium=y")
    b = normalize_discovery_url("https://example.com/jobs/1")
    assert a == b
    stripe = normalize_discovery_url("https://stripe.com/jobs/search?gh_jid=123")
    assert "gh_jid=123" in stripe


def test_discovery_cannot_authorize_with_new_providers(session: Session):
    settings = _settings()

    class P:
        name = "ashby"

        def search(self, query):  # noqa: ANN001
            return [
                _strong("X", "https://example.com/x", "ashby", "as:x"),
            ]

    agent = DiscoveryAgent(session, settings=settings, providers=[P()])
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    session.commit()
    assert result.surfaced_result_count >= 1
    assert session.query(Approval).count() == 0
    assert not any(
        i.agent_type == AgentType.RESUME.value for i in session.query(AgentWorkItem).all()
    )
    for row in [session.get(DiscoveryResult, i) for i in result.surfaced_ids]:
        if row and row.job_id:
            assert ApprovalService(session).can_prepare_application(row.job_id) is False


def test_scout_uses_structured_ashby_content(session: Session, monkeypatch):
    settings = _settings()
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    row = DiscoveryResult(
        discovery_run_id=run.id,
        provider="ashby",
        external_id="ex:1",
        source_name="ashby:ex",
        title="Backend Software Engineer",
        company="Example",
        location="Chandler, AZ",
        work_arrangement="hybrid",
        job_url="https://jobs.ashbyhq.com/ex/1",
        canonical_url="https://jobs.ashbyhq.com/ex/1",
        description_snippet="Java backend",
        description_full=(
            "Backend Software Engineer — build Java services, REST APIs, SQL, AWS. "
            "Hybrid in Chandler, AZ. Required: Java, Spring Boot, SQL. "
            + ("Own services end-to-end. " * 30)
        ),
        discovery_score=90,
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
    assert outcome.ok is True
    assert outcome.used_structured_content is True
    assert ApprovalService(session).can_prepare_application(outcome.job.id) is False

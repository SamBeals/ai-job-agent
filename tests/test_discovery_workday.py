"""Phase 3.5 — Workday Discovery provider (mocked HTTP)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy.orm import Session

from app.agents.discovery.agent import DiscoveryAgent, search_providers
from app.agents.discovery.boards import load_discovery_boards
from app.agents.discovery.dedupe import dedupe_within_run, find_prior_identity, should_block_resurface
from app.agents.discovery.factory import build_discovery_providers
from app.agents.discovery.filters import prefilter_candidate
from app.agents.discovery.providers.fake import FakeDiscoveryProvider
from app.agents.discovery.providers.workday import WorkdayBoard, WorkdayDiscoveryProvider
from app.agents.discovery.ranking import score_candidate
from app.agents.discovery.scout_bridge import scout_discovery_result
from app.agents.scout.profile_loader import load_candidate_profile
from app.config import Settings
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
        discovery_workday_enabled=True,
        llm_provider="mock",
        discord_agent_webhook_url="",
    )
    base.update(overrides)
    return Settings(**base)


def _query(**overrides) -> DiscoveryQuery:
    base = dict(
        role_terms=["Backend Software Engineer", "Software Engineer"],
        location_terms=["Chandler, Arizona", "Phoenix, Arizona"],
        local_location_terms=["Chandler, AZ", "Phoenix, AZ"],
        include_remote=True,
        prioritize_local_search=True,
        max_raw_results=50,
    )
    base.update(overrides)
    return DiscoveryQuery(**base)


def _board(**kw) -> WorkdayBoard:
    base = dict(
        company="Intel",
        host="intel.wd1.myworkdayjobs.com",
        tenant="intel",
        site="External",
        metro="phoenix",
    )
    base.update(kw)
    return WorkdayBoard(**base)


LISTING = {
    "total": 2,
    "jobPostings": [
        {
            "title": "Backend Software Engineer",
            "externalPath": "/job/US-Arizona-Chandler/Backend-Software-Engineer_JR12345",
            "locationsText": "US, Arizona, Chandler",
            "postedOn": "2026-08-01T00:00:00.000Z",
        },
        {
            "title": "Retail Associate",
            "externalPath": "/job/US-Arizona-Phoenix/Retail-Associate_R1",
            "locationsText": "US, Arizona, Phoenix",
            "postedOn": "Posted Today",
        },
    ],
}

DETAIL = {
    "jobPostingInfo": {
        "id": "wd-posting-abc",
        "jobPostingId": "wd-posting-abc",
        "jobReqId": "JR12345",
        "title": "Backend Software Engineer",
        "location": "US, Arizona, Chandler",
        "country": {"descriptor": "United States of America"},
        "jobDescription": "<p>Java Spring Boot REST APIs AWS Kubernetes</p>",
        "externalUrl": (
            "https://intel.wd1.myworkdayjobs.com/External/job/"
            "US-Arizona-Chandler/Backend-Software-Engineer_JR12345"
        ),
        "startDate": "2026-08-01T12:00:00.000Z",
        "timeType": "Full time",
        "remoteType": None,
        "posted": True,
    }
}


class _FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=MagicMock(), response=MagicMock(status_code=self.status_code)
            )

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_workday_registry_entry_parses():
    boards = load_discovery_boards("config/discovery_boards.json")
    wd = boards["workday"]
    assert wd
    intel = next(e for e in wd if e.company == "Intel")
    assert intel.host == "intel.wd1.myworkdayjobs.com"
    assert intel.tenant == "intel"
    assert intel.site == "External"
    assert intel.metro == "phoenix"
    assert any(e.company == "Choice Hotels" for e in wd)


def test_disabled_workday_entry_ignored(tmp_path: Path):
    path = tmp_path / "boards.json"
    path.write_text(
        json.dumps(
            {
                "greenhouse": [],
                "lever": [],
                "ashby": [],
                "workday": [
                    {
                        "company": "Disabled",
                        "host": "x.wd1.myworkdayjobs.com",
                        "tenant": "x",
                        "site": "External",
                        "enabled": False,
                    },
                    {
                        "company": "Enabled",
                        "host": "y.wd1.myworkdayjobs.com",
                        "tenant": "y",
                        "site": "External",
                        "enabled": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    boards = load_discovery_boards(path)
    assert [e.company for e in boards["workday"]] == ["Enabled"]


def test_workday_returns_raw_discovery_result(monkeypatch, profile):
    posts = {"calls": 0}

    def fake_request(self, method, url, **kwargs):  # noqa: ANN001
        posts["calls"] += 1
        if method == "POST" and url.endswith("/jobs"):
            return _FakeResp(200, LISTING)
        if method == "GET" and "/job/" in url:
            return _FakeResp(200, DETAIL)
        return _FakeResp(404, text="no")

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    # httpx Client.post/get call request
    monkeypatch.setattr(
        httpx.Client,
        "post",
        lambda self, url, **kw: fake_request(self, "POST", url, **kw),
    )
    monkeypatch.setattr(
        httpx.Client,
        "get",
        lambda self, url, **kw: fake_request(self, "GET", url, **kw),
    )

    provider = WorkdayDiscoveryProvider(boards=[_board()], fetch_details=True)
    results = provider.search(_query())
    assert results
    raw = results[0]
    assert isinstance(raw, RawDiscoveryResult)
    assert raw.provider == "workday"
    assert raw.external_id == "wd-posting-abc"
    assert raw.raw_metadata.get("requisition_id") == "JR12345"
    assert raw.title == "Backend Software Engineer"
    assert raw.company == "Intel"
    assert "Chandler" in (raw.location_text or "")
    assert raw.description_full and "Java" in raw.description_full
    assert raw.canonical_url and "myworkdayjobs.com" in raw.canonical_url
    assert raw.job_url == raw.canonical_url
    assert raw.published_at is not None
    assert raw.salary_min is None and raw.salary_max is None


def test_workday_missing_optional_fields_ok(monkeypatch):
    listing = {
        "total": 1,
        "jobPostings": [
            {
                "title": "Software Engineer",
                "externalPath": "/job/US-Remote/Software-Engineer_JR9",
                "locationsText": "United States",
            }
        ],
    }

    monkeypatch.setattr(
        httpx.Client,
        "post",
        lambda self, url, **kw: _FakeResp(200, listing),
    )
    monkeypatch.setattr(
        httpx.Client,
        "get",
        lambda self, url, **kw: _FakeResp(404, text="missing"),
    )
    provider = WorkdayDiscoveryProvider(boards=[_board()], fetch_details=True)
    results = provider.search(_query(role_terms=["Software Engineer"]))
    assert len(results) == 1
    assert results[0].description_full is None
    assert results[0].salary_min is None


def test_workday_pagination_terminates_and_respects_max(monkeypatch):
    calls = {"n": 0}

    def post(self, url, **kw):  # noqa: ANN001
        calls["n"] += 1
        offset = (kw.get("json") or {}).get("offset", 0)
        if offset >= 40:
            return _FakeResp(200, {"total": 100, "jobPostings": []})
        posts = [
            {
                "title": f"Software Engineer {offset + i}",
                "externalPath": f"/job/US/SE_{offset + i}",
                "locationsText": "US, Arizona, Phoenix",
            }
            for i in range(20)
        ]
        return _FakeResp(200, {"total": 100, "jobPostings": posts})

    monkeypatch.setattr(httpx.Client, "post", post)
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **kw: _FakeResp(404))
    provider = WorkdayDiscoveryProvider(
        boards=[_board()],
        max_jobs_per_board=25,
        max_pages_per_board=5,
        fetch_details=False,
    )
    results = provider.search(_query(max_raw_results=25, role_terms=["Software Engineer"]))
    assert len(results) <= 25
    assert calls["n"] <= 5
    paths = [r.raw_metadata.get("external_path") for r in results]
    assert len(paths) == len(set(paths))


def test_workday_malformed_response_fails_safely(monkeypatch):
    monkeypatch.setattr(
        httpx.Client,
        "post",
        lambda self, url, **kw: _FakeResp(200, payload="not-a-dict"),
    )
    provider = WorkdayDiscoveryProvider(boards=[_board()], fetch_details=False)
    with pytest.raises(Exception):
        provider.search(_query())


def test_workday_rate_limit_handled(monkeypatch):
    def post(self, url, **kw):  # noqa: ANN001
        resp = _FakeResp(429, text="slow down")
        resp.headers = {"content-type": "text/plain", "Retry-After": "30"}
        return resp

    monkeypatch.setattr(httpx.Client, "post", post)
    provider = WorkdayDiscoveryProvider(boards=[_board()], fetch_details=False)
    with pytest.raises(httpx.HTTPStatusError):
        provider.search(_query())


def test_one_failed_workday_board_does_not_kill_others(monkeypatch):
    def post(self, url, **kw):  # noqa: ANN001
        if "bad.wd1" in url:
            return _FakeResp(500, text="boom")
        return _FakeResp(200, LISTING)

    monkeypatch.setattr(httpx.Client, "post", post)
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **kw: _FakeResp(200, DETAIL))
    provider = WorkdayDiscoveryProvider(
        boards=[
            _board(company="Bad", host="bad.wd1.myworkdayjobs.com", tenant="bad"),
            _board(),
        ],
        fetch_details=True,
    )
    results = provider.search(_query())
    assert any(r.company == "Intel" for r in results)


def test_workday_provider_failure_isolated_from_others():
    class BoomWorkday:
        name = "workday"

        def search(self, query):  # noqa: ANN001
            raise RuntimeError("workday down")

    class OkGreenhouse:
        name = "greenhouse"

        def search(self, query):  # noqa: ANN001
            return FakeDiscoveryProvider().search(query)

    outcome = search_providers(
        [BoomWorkday(), OkGreenhouse()],
        _query(max_raw_results=10),
        run_id=1,
    )
    assert outcome.providers_failed == 1
    assert outcome.providers_ok == 1
    assert outcome.raw_results


def test_workday_geo_and_salary_and_quality(profile):
    phoenix = RawDiscoveryResult(
        provider="workday",
        source_name="Intel",
        external_id="1",
        title="Backend Software Engineer",
        company="Intel",
        location_text="US, Arizona, Chandler",
        work_arrangement="hybrid",
        salary_min=140000,
        salary_max=170000,
        salary_period="year",
        description_snippet="Java Spring Boot",
        description_full="Java Spring Boot REST",
        job_url="https://intel.wd1.myworkdayjobs.com/External/job/x_JR1",
        canonical_url="https://intel.wd1.myworkdayjobs.com/External/job/x_JR1",
    )
    tempe = phoenix.model_copy(
        update={
            "external_id": "2",
            "location_text": "Tempe, AZ",
            "canonical_url": phoenix.canonical_url + "2",
            "job_url": phoenix.job_url + "2",
        }
    )
    foreign = phoenix.model_copy(
        update={
            "external_id": "3",
            "location_text": "Israel, Haifa",
            "canonical_url": phoenix.canonical_url + "3",
            "job_url": phoenix.job_url + "3",
        }
    )
    nyc = phoenix.model_copy(
        update={
            "external_id": "4",
            "location_text": "New York, NY",
            "work_arrangement": "onsite",
            "canonical_url": phoenix.canonical_url + "4",
            "job_url": phoenix.job_url + "4",
        }
    )
    remote = phoenix.model_copy(
        update={
            "external_id": "5",
            "location_text": "United States — Remote",
            "work_arrangement": "remote",
            "canonical_url": phoenix.canonical_url + "5",
            "job_url": phoenix.job_url + "5",
        }
    )
    low_sal = phoenix.model_copy(
        update={
            "external_id": "6",
            "salary_min": 70000,
            "salary_max": 80000,
            "canonical_url": phoenix.canonical_url + "6",
            "job_url": phoenix.job_url + "6",
        }
    )

    assert prefilter_candidate(profile, phoenix).filtered is False
    assert prefilter_candidate(profile, tempe).filtered is False
    assert prefilter_candidate(profile, foreign).filter_reason == "FOREIGN_LOCATION"
    assert prefilter_candidate(profile, nyc).filter_reason == "NONLOCAL_ONSITE"
    assert prefilter_candidate(profile, remote).filtered is False
    assert prefilter_candidate(profile, low_sal).filter_reason == "SALARY_BELOW_MINIMUM"

    scored = score_candidate(profile, prefilter_candidate(profile, phoenix))
    assert scored.discovery_score >= 45


def test_workday_dedupe_against_broad_copy(profile):
    wd = score_candidate(
        profile,
        prefilter_candidate(
            profile,
            RawDiscoveryResult(
                provider="workday",
                source_name="Intel",
                external_id="wd1",
                title="Backend Software Engineer",
                company="Intel",
                location_text="Chandler, AZ",
                work_arrangement="hybrid",
                salary_min=140000,
                salary_max=160000,
                salary_period="year",
                description_snippet="Java",
                job_url="https://intel.wd1.myworkdayjobs.com/External/job/x?utm_source=muse",
                canonical_url="https://intel.wd1.myworkdayjobs.com/External/job/x?utm_source=muse",
            ),
        ),
    )
    muse = score_candidate(
        profile,
        prefilter_candidate(
            profile,
            RawDiscoveryResult(
                provider="muse",
                source_name="muse",
                external_id="m1",
                title="Backend Software Engineer",
                company="Intel",
                location_text="Chandler, AZ",
                work_arrangement="hybrid",
                salary_min=140000,
                salary_max=160000,
                salary_period="year",
                description_snippet="Java",
                job_url="https://intel.wd1.myworkdayjobs.com/External/job/x",
                canonical_url="https://intel.wd1.myworkdayjobs.com/External/job/x",
            ),
        ),
    )
    out = dedupe_within_run([wd, muse])
    assert len(out) == 1


def test_different_requisitions_remain_separate(profile):
    a = score_candidate(
        profile,
        prefilter_candidate(
            profile,
            RawDiscoveryResult(
                provider="workday",
                source_name="Intel",
                external_id="JR1",
                title="Software Engineer",
                company="Intel",
                location_text="Phoenix, AZ",
                work_arrangement="hybrid",
                salary_min=140000,
                salary_max=160000,
                salary_period="year",
                description_snippet="Java",
                job_url="https://intel.wd1.myworkdayjobs.com/External/job/a_JR1",
                canonical_url="https://intel.wd1.myworkdayjobs.com/External/job/a_JR1",
            ),
        ),
    )
    b = score_candidate(
        profile,
        prefilter_candidate(
            profile,
            RawDiscoveryResult(
                provider="workday",
                source_name="Intel",
                external_id="JR2",
                title="Software Engineer",
                company="Intel",
                location_text="Phoenix, AZ",
                work_arrangement="hybrid",
                salary_min=140000,
                salary_max=160000,
                salary_period="year",
                description_snippet="Java",
                job_url="https://intel.wd1.myworkdayjobs.com/External/job/b_JR2",
                canonical_url="https://intel.wd1.myworkdayjobs.com/External/job/b_JR2",
            ),
        ),
    )
    assert len(dedupe_within_run([a, b])) == 2


@pytest.mark.parametrize("status", ["SURFACED", "SCOUTED", "DISMISSED"])
def test_workday_cross_run_suppression(session: Session, profile, status):
    run = DiscoveryRun(status=DiscoveryRunStatus.COMPLETED.value)
    session.add(run)
    session.flush()
    prior = DiscoveryResult(
        discovery_run_id=run.id,
        provider="workday",
        external_id="old",
        source_name="Intel",
        title="Backend Software Engineer",
        company="Intel",
        location="Chandler, AZ",
        job_url="https://intel.wd1.myworkdayjobs.com/External/job/x",
        canonical_url="https://intel.wd1.myworkdayjobs.com/External/job/x",
        discovery_score=70,
        status=status,
    )
    session.add(prior)
    session.flush()
    raw = RawDiscoveryResult(
        provider="workday",
        source_name="Intel",
        external_id="new",
        title="Backend Software Engineer",
        company="Intel",
        location_text="Chandler, AZ",
        job_url="https://intel.wd1.myworkdayjobs.com/External/job/x",
        canonical_url="https://intel.wd1.myworkdayjobs.com/External/job/x",
    )
    found = find_prior_identity(session, raw)
    assert found is not None
    assert should_block_resurface(found) is True


def test_workday_structured_description_reaches_scout_bridge(
    session: Session, monkeypatch, profile
):
    settings = _settings()
    raw = RawDiscoveryResult(
        provider="workday",
        source_name="Choice Hotels",
        external_id="R21440",
        title="Backend Software Engineer",
        company="Choice Hotels",
        location_text="Scottsdale, AZ",
        work_arrangement="hybrid",
        salary_min=130000,
        salary_max=160000,
        salary_period="year",
        description_snippet="Java backend platform services",
        description_full=(
            "Java Spring Boot REST APIs for hotel platform services. "
            "Own backend microservices, SQL data access, AWS deployments, "
            "Kubernetes operations, CI/CD pipelines, and on-call support for "
            "enterprise lodging systems. Collaborate with product and platform "
            "teams on scalable APIs used across Choice Hotels digital products."
        ),
        job_url="https://choicehotels.wd5.myworkdayjobs.com/External/job/x_R21440",
        canonical_url="https://choicehotels.wd5.myworkdayjobs.com/External/job/x_R21440",
    )

    class OneShot:
        name = "workday"

        def search(self, query):  # noqa: ANN001
            return [raw]

    agent = DiscoveryAgent(session, settings=settings, providers=[OneShot()])
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    session.commit()
    assert result.surfaced_result_count >= 1
    row = session.query(DiscoveryResult).filter_by(discovery_run_id=run.id).one()

    from app.agents.scout.ingestion.models import IngestionError

    monkeypatch.setattr(
        "app.agents.scout.ingestion.service.JobIngestionService.ingest_url",
        lambda *a, **k: (_ for _ in ()).throw(IngestionError("blocked", code="FETCH_BLOCKED")),
    )
    outcome = scout_discovery_result(session, row.id, settings=settings)
    session.commit()
    assert outcome.ok
    assert outcome.used_structured_content is True
    assert session.query(Approval).count() == 0
    assert session.query(ApplicationPipeline).count() == 0
    assert session.query(SubmissionAuthorization).count() == 0
    assert ApprovalService(session).can_prepare_application(outcome.job.id) is False


def test_factory_includes_workday_when_enabled():
    settings = _settings(
        discovery_provider="auto",
        discovery_workday_enabled=True,
        discovery_greenhouse_enabled=False,
        discovery_lever_enabled=False,
        discovery_ashby_enabled=False,
        discovery_remotive_enabled=False,
        discovery_enable_remotive=False,
        discovery_muse_enabled=False,
        discovery_adzuna_enabled=False,
    )
    providers = build_discovery_providers(settings)
    assert any(p.name == "workday" for p in providers)


def test_workday_partial_when_mixed_with_success(session: Session):
    class Boom:
        name = "workday"

        def search(self, query):  # noqa: ANN001
            raise RuntimeError("down")

    settings = _settings()
    agent = DiscoveryAgent(
        session,
        settings=settings,
        providers=[Boom(), FakeDiscoveryProvider()],
    )
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    assert result.status == DiscoveryRunStatus.PARTIAL.value
    assert result.success

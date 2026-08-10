"""Phase 3.6 — Oracle Recruiting Discovery provider (mocked HTTP)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy.orm import Session

from app.agents.discovery.agent import DiscoveryAgent, search_providers
from app.agents.discovery.boards import load_discovery_boards
from app.agents.discovery.dedupe import (
    dedupe_within_run,
    find_prior_identity,
    should_block_resurface,
)
from app.agents.discovery.factory import build_discovery_providers
from app.agents.discovery.filters import prefilter_candidate
from app.agents.discovery.providers.fake import FakeDiscoveryProvider
from app.agents.discovery.providers.oracle import (
    OracleBoard,
    OracleRecruitingDiscoveryProvider,
)
from app.agents.discovery.ranking import score_candidate
from app.agents.discovery.scout_bridge import scout_discovery_result
from app.agents.scout.profile_loader import load_candidate_profile
from app.config import Settings
from app.models.approval import Approval
from app.models.discovery import DiscoveryResult, DiscoveryRun
from app.models.pipeline import ApplicationPipeline
from app.models.submission_authorization import SubmissionAuthorization
from app.schemas.discovery import (
    DiscoveryQuery,
    DiscoveryResultStatus,
    DiscoveryRunStatus,
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
        discovery_oracle_enabled=True,
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


def _board(**kw) -> OracleBoard:
    base = dict(
        company="American Express",
        host="egug.fa.us2.oraclecloud.com",
        site_number="CX_1",
        site_path="CX_1",
        career_base_url="https://careers.americanexpress.com",
        metro="phoenix",
        location_facet_ids=("300000007667464",),
    )
    base.update(kw)
    return OracleBoard(**base)


def _search_payload(reqs: list[dict], *, total: int | None = None, offset: int = 0):
    return {
        "items": [
            {
                "SearchId": 1,
                "TotalJobsCount": total if total is not None else len(reqs),
                "Offset": offset,
                "Limit": 25,
                "requisitionList": reqs,
            }
        ],
        "count": 1,
        "hasMore": False,
    }


LISTING_REQ = {
    "Id": "26011927",
    "Title": "Software Engineer II",
    "PostedDate": "2026-08-06",
    "PrimaryLocation": "Phoenix, AZ, United States",
    "PrimaryLocationCountry": "US",
    "WorkplaceType": "Hybrid",
    "WorkplaceTypeCode": "ORA_HYBRID",
    "ShortDescriptionStr": "",
    "GeographyId": 300000007667464,
    "workLocation": [
        {
            "TownOrCity": "Phoenix",
            "Region2": "AZ",
            "Country": "US",
        }
    ],
}

DETAIL = {
    "Id": "26011927",
    "Title": "Software Engineer II",
    "PostedDate": "2026-08-06",
    "PrimaryLocation": "Phoenix, AZ, United States",
    "PrimaryLocationCountry": "US",
    "WorkplaceType": "Hybrid",
    "WorkplaceTypeCode": "ORA_HYBRID",
    "ExternalDescriptionStr": (
        "<p>Joining Amex Tech means building Java Spring Boot REST APIs on AWS.</p>"
    ),
    "ExternalResponsibilitiesStr": (
        "<ul><li>Designs, develops, tests backend services</li></ul>"
    ),
    "ExternalQualificationsStr": (
        "<ul><li>Degree in computer science; Kubernetes Docker CI/CD</li></ul>"
    ),
}


class _FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {"content-type": "application/json"}
        self.content = (
            json.dumps(payload).encode()
            if payload is not None and not isinstance(payload, str)
            else (text or "").encode()
        )

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err",
                request=MagicMock(),
                response=MagicMock(status_code=self.status_code),
            )

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        if isinstance(self._payload, str):
            raise ValueError("no json")
        return self._payload


def _patch_client(monkeypatch, handler):
    monkeypatch.setattr(httpx.Client, "get", handler)


def test_oracle_registry_entry_parses():
    boards = load_discovery_boards("config/discovery_boards.json")
    oracle = boards["oracle"]
    assert oracle
    amex = next(e for e in oracle if e.company == "American Express")
    assert amex.host == "egug.fa.us2.oraclecloud.com"
    assert amex.tenant == "CX_1"
    assert amex.site == "CX_1"
    assert amex.career_base_url == "https://careers.americanexpress.com"
    assert amex.metro == "phoenix"
    assert "300000007667464" in amex.location_facet_ids
    assert any(e.company == "Honeywell" for e in oracle)


def test_disabled_oracle_board_ignored(tmp_path: Path):
    path = tmp_path / "boards.json"
    path.write_text(
        json.dumps(
            {
                "greenhouse": [],
                "lever": [],
                "ashby": [],
                "workday": [],
                "oracle": [
                    {
                        "company": "Disabled",
                        "host": "x.fa.us2.oraclecloud.com",
                        "site_number": "CX_1",
                        "site_path": "CX_1",
                        "career_base_url": "https://example.com",
                        "enabled": False,
                    },
                    {
                        "company": "Enabled",
                        "host": "y.fa.us2.oraclecloud.com",
                        "site_number": "CX_1",
                        "site_path": "Y",
                        "career_base_url": "https://careers.example.com",
                        "enabled": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    boards = load_discovery_boards(path)
    assert [e.company for e in boards["oracle"]] == ["Enabled"]


def test_oracle_valid_search_normalizes(monkeypatch, profile):
    def get(self, url, **kw):  # noqa: ANN001
        if "JobRequisitionDetails" in url:
            return _FakeResp(200, {"items": [DETAIL]})
        return _FakeResp(200, _search_payload([LISTING_REQ], total=1))

    _patch_client(monkeypatch, get)
    provider = OracleRecruitingDiscoveryProvider(boards=[_board()], fetch_details=True)
    results = provider.search(_query())
    assert results
    raw = results[0]
    assert isinstance(raw, RawDiscoveryResult)
    assert raw.provider == "oracle"
    assert raw.external_id == "26011927"
    assert raw.raw_metadata.get("requisition_id") == "26011927"
    assert raw.title == "Software Engineer II"
    assert raw.company == "American Express"
    assert "Phoenix" in (raw.location_text or "")
    assert raw.raw_metadata.get("primary_location_country") == "US"
    assert raw.description_full and "Java" in raw.description_full
    assert raw.published_at is not None
    assert raw.canonical_url == (
        "https://careers.americanexpress.com/en/sites/CX_1/job/26011927"
    )
    assert raw.job_url == raw.canonical_url
    assert raw.raw_metadata.get("application_url") == raw.canonical_url
    assert raw.salary_min is None and raw.salary_max is None
    assert raw.work_arrangement == "hybrid"


def test_oracle_missing_description_and_location_ok(monkeypatch):
    req = {
        "Id": "99",
        "Title": "Software Engineer",
        "PostedDate": None,
        "PrimaryLocation": None,
        "PrimaryLocationCountry": None,
        "WorkplaceType": None,
        "ShortDescriptionStr": "",
    }

    def get(self, url, **kw):  # noqa: ANN001
        if "JobRequisitionDetails" in url:
            return _FakeResp(404, text="missing")
        return _FakeResp(200, _search_payload([req], total=1))

    _patch_client(monkeypatch, get)
    provider = OracleRecruitingDiscoveryProvider(boards=[_board()], fetch_details=True)
    results = provider.search(_query(role_terms=["Software Engineer"]))
    assert len(results) == 1
    assert results[0].description_full is None
    assert results[0].location_text is None
    assert results[0].salary_min is None


def test_oracle_empty_result_succeeds(monkeypatch):
    def get(self, url, **kw):  # noqa: ANN001
        return _FakeResp(200, _search_payload([], total=0))

    _patch_client(monkeypatch, get)
    provider = OracleRecruitingDiscoveryProvider(boards=[_board()], fetch_details=False)
    assert provider.search(_query()) == []


def test_oracle_pagination_terminates_and_respects_max(monkeypatch):
    calls = {"n": 0}

    def get(self, url, **kw):  # noqa: ANN001
        if "JobRequisitionDetails" in url:
            return _FakeResp(404, text="no")
        calls["n"] += 1
        finder = (kw.get("params") or {}).get("finder") or ""
        # Parse offset from finder
        offset = 0
        for part in finder.split(","):
            if part.startswith("offset="):
                offset = int(part.split("=", 1)[1])
        if offset >= 50:
            return _FakeResp(200, _search_payload([], total=100, offset=offset))
        reqs = [
            {
                "Id": str(offset + i + 1),
                "Title": f"Software Engineer {offset + i}",
                "PrimaryLocation": "Phoenix, AZ, United States",
                "PrimaryLocationCountry": "US",
                "WorkplaceType": "Hybrid",
                "PostedDate": "2026-08-01",
            }
            for i in range(25)
        ]
        return _FakeResp(200, _search_payload(reqs, total=100, offset=offset))

    _patch_client(monkeypatch, get)
    provider = OracleRecruitingDiscoveryProvider(
        boards=[_board(location_facet_ids=())],
        max_jobs_per_board=30,
        max_pages_per_board=5,
        fetch_details=False,
    )
    results = provider.search(
        _query(max_raw_results=30, role_terms=["Software Engineer"])
    )
    assert len(results) <= 30
    assert calls["n"] <= 5
    ids = [r.external_id for r in results]
    assert len(ids) == len(set(ids))


def test_oracle_duplicate_page_does_not_duplicate_results(monkeypatch):
    def get(self, url, **kw):  # noqa: ANN001
        if "JobRequisitionDetails" in url:
            return _FakeResp(404, text="no")
        # Always return the same page (simulates stuck offset)
        return _FakeResp(200, _search_payload([LISTING_REQ], total=100))

    _patch_client(monkeypatch, get)
    provider = OracleRecruitingDiscoveryProvider(
        boards=[_board(location_facet_ids=())],
        max_jobs_per_board=40,
        max_pages_per_board=5,
        fetch_details=False,
    )
    results = provider.search(_query(role_terms=["Software Engineer"]))
    assert len(results) == 1


def test_oracle_malformed_response_fails_safely(monkeypatch):
    def get(self, url, **kw):  # noqa: ANN001
        return _FakeResp(200, payload="not-a-dict")

    _patch_client(monkeypatch, get)
    provider = OracleRecruitingDiscoveryProvider(boards=[_board()], fetch_details=False)
    with pytest.raises(Exception):
        provider.search(_query())


def test_oracle_timeout_fails_safely(monkeypatch):
    def get(self, url, **kw):  # noqa: ANN001
        raise httpx.TimeoutException("timeout")

    _patch_client(monkeypatch, get)
    provider = OracleRecruitingDiscoveryProvider(boards=[_board()], fetch_details=False)
    with pytest.raises(httpx.TimeoutException):
        provider.search(_query())


def test_oracle_rate_limit_handled(monkeypatch):
    def get(self, url, **kw):  # noqa: ANN001
        resp = _FakeResp(429, text="slow down")
        resp.headers = {"content-type": "text/plain", "Retry-After": "30"}
        return resp

    _patch_client(monkeypatch, get)
    provider = OracleRecruitingDiscoveryProvider(boards=[_board()], fetch_details=False)
    with pytest.raises(httpx.HTTPStatusError):
        provider.search(_query())


def test_one_failed_oracle_board_does_not_kill_others(monkeypatch):
    def get(self, url, **kw):  # noqa: ANN001
        if "bad.fa" in url:
            return _FakeResp(500, text="boom")
        if "JobRequisitionDetails" in url:
            return _FakeResp(200, {"items": [DETAIL]})
        return _FakeResp(200, _search_payload([LISTING_REQ], total=1))

    _patch_client(monkeypatch, get)
    provider = OracleRecruitingDiscoveryProvider(
        boards=[
            _board(company="Bad", host="bad.fa.us2.oraclecloud.com"),
            _board(),
        ],
        fetch_details=True,
    )
    results = provider.search(_query())
    assert any(r.company == "American Express" for r in results)


def test_oracle_provider_failure_isolated_from_others():
    class BoomOracle:
        name = "oracle"

        def search(self, query):  # noqa: ANN001
            raise RuntimeError("oracle down")

    class OkGreenhouse:
        name = "greenhouse"

        def search(self, query):  # noqa: ANN001
            return FakeDiscoveryProvider().search(query)

    outcome = search_providers(
        [BoomOracle(), OkGreenhouse()],
        _query(max_raw_results=10),
        run_id=1,
    )
    assert outcome.providers_failed == 1
    assert outcome.providers_ok == 1
    assert outcome.raw_results


def test_oracle_geo_salary_quality_gates(profile):
    phoenix = RawDiscoveryResult(
        provider="oracle",
        source_name="American Express",
        external_id="1",
        title="Backend Software Engineer",
        company="American Express",
        location_text="Phoenix, AZ, United States",
        work_arrangement="hybrid",
        salary_min=140000,
        salary_max=170000,
        salary_period="year",
        description_snippet="Java Spring Boot",
        description_full="Java Spring Boot REST",
        job_url="https://careers.americanexpress.com/en/sites/CX_1/job/1",
        canonical_url="https://careers.americanexpress.com/en/sites/CX_1/job/1",
    )
    chandler = phoenix.model_copy(
        update={
            "external_id": "2",
            "location_text": "Chandler, AZ, United States",
            "canonical_url": phoenix.canonical_url + "2",
            "job_url": phoenix.job_url + "2",
        }
    )
    tempe = phoenix.model_copy(
        update={
            "external_id": "3",
            "location_text": "Tempe, AZ",
            "canonical_url": phoenix.canonical_url + "3",
            "job_url": phoenix.job_url + "3",
        }
    )
    scottsdale = phoenix.model_copy(
        update={
            "external_id": "4",
            "location_text": "Scottsdale, AZ, United States",
            "canonical_url": phoenix.canonical_url + "4",
            "job_url": phoenix.job_url + "4",
        }
    )
    foreign = phoenix.model_copy(
        update={
            "external_id": "5",
            "location_text": "London, United Kingdom",
            "canonical_url": phoenix.canonical_url + "5",
            "job_url": phoenix.job_url + "5",
        }
    )
    nyc = phoenix.model_copy(
        update={
            "external_id": "6",
            "location_text": "New York, NY, United States",
            "work_arrangement": "onsite",
            "canonical_url": phoenix.canonical_url + "6",
            "job_url": phoenix.job_url + "6",
        }
    )
    remote = phoenix.model_copy(
        update={
            "external_id": "7",
            "location_text": "United States — Remote",
            "work_arrangement": "remote",
            "canonical_url": phoenix.canonical_url + "7",
            "job_url": phoenix.job_url + "7",
        }
    )
    low_sal = phoenix.model_copy(
        update={
            "external_id": "8",
            "salary_min": 70000,
            "salary_max": 80000,
            "canonical_url": phoenix.canonical_url + "8",
            "job_url": phoenix.job_url + "8",
        }
    )

    assert prefilter_candidate(profile, phoenix).filtered is False
    assert prefilter_candidate(profile, chandler).filtered is False
    assert prefilter_candidate(profile, tempe).filtered is False
    assert prefilter_candidate(profile, scottsdale).filtered is False
    assert prefilter_candidate(profile, foreign).filter_reason == "FOREIGN_LOCATION"
    assert prefilter_candidate(profile, nyc).filter_reason == "NONLOCAL_ONSITE"
    assert prefilter_candidate(profile, remote).filtered is False
    assert prefilter_candidate(profile, low_sal).filter_reason == "SALARY_BELOW_MINIMUM"

    scored = score_candidate(profile, prefilter_candidate(profile, phoenix))
    assert scored.discovery_score >= 45


def test_oracle_cannot_bypass_score_threshold(profile):
    weak = RawDiscoveryResult(
        provider="oracle",
        source_name="American Express",
        external_id="w",
        title="Retail Banking Specialist",
        company="American Express",
        location_text="Phoenix, AZ",
        work_arrangement="onsite",
        description_snippet="customer service",
        job_url="https://careers.americanexpress.com/en/sites/CX_1/job/w",
        canonical_url="https://careers.americanexpress.com/en/sites/CX_1/job/w",
    )
    scored = score_candidate(profile, prefilter_candidate(profile, weak))
    # Either filtered earlier or below surface threshold
    assert scored.filtered or scored.discovery_score < 45


def test_oracle_dedupe_against_muse_and_other(profile):
    oracle = score_candidate(
        profile,
        prefilter_candidate(
            profile,
            RawDiscoveryResult(
                provider="oracle",
                source_name="American Express",
                external_id="26011927",
                title="Software Engineer II",
                company="American Express",
                location_text="Phoenix, AZ",
                work_arrangement="hybrid",
                salary_min=140000,
                salary_max=160000,
                salary_period="year",
                description_snippet="Java",
                job_url=(
                    "https://careers.americanexpress.com/en/sites/CX_1/job/26011927"
                    "?utm_source=muse"
                ),
                canonical_url=(
                    "https://careers.americanexpress.com/en/sites/CX_1/job/26011927"
                    "?utm_source=muse"
                ),
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
                title="Software Engineer II",
                company="American Express",
                location_text="Phoenix, AZ",
                work_arrangement="hybrid",
                salary_min=140000,
                salary_max=160000,
                salary_period="year",
                description_snippet="Java",
                job_url="https://careers.americanexpress.com/en/sites/CX_1/job/26011927",
                canonical_url=(
                    "https://careers.americanexpress.com/en/sites/CX_1/job/26011927"
                ),
            ),
        ),
    )
    greenhouse = score_candidate(
        profile,
        prefilter_candidate(
            profile,
            RawDiscoveryResult(
                provider="greenhouse",
                source_name="American Express",
                external_id="g1",
                title="Software Engineer II",
                company="American Express",
                location_text="Phoenix, AZ",
                work_arrangement="hybrid",
                salary_min=140000,
                salary_max=160000,
                salary_period="year",
                description_snippet="Java",
                job_url="https://careers.americanexpress.com/en/sites/CX_1/job/26011927",
                canonical_url=(
                    "https://careers.americanexpress.com/en/sites/CX_1/job/26011927"
                ),
            ),
        ),
    )
    assert len(dedupe_within_run([oracle, muse])) == 1
    assert len(dedupe_within_run([oracle, greenhouse])) == 1


def test_oracle_distinct_requisitions_remain_distinct(profile):
    a = score_candidate(
        profile,
        prefilter_candidate(
            profile,
            RawDiscoveryResult(
                provider="oracle",
                source_name="American Express",
                external_id="1",
                title="Software Engineer",
                company="American Express",
                location_text="Phoenix, AZ",
                work_arrangement="hybrid",
                salary_min=140000,
                salary_max=160000,
                salary_period="year",
                description_snippet="Java",
                job_url="https://careers.americanexpress.com/en/sites/CX_1/job/1",
                canonical_url="https://careers.americanexpress.com/en/sites/CX_1/job/1",
            ),
        ),
    )
    b = score_candidate(
        profile,
        prefilter_candidate(
            profile,
            RawDiscoveryResult(
                provider="oracle",
                source_name="American Express",
                external_id="2",
                title="Software Engineer",
                company="American Express",
                location_text="Phoenix, AZ",
                work_arrangement="hybrid",
                salary_min=140000,
                salary_max=160000,
                salary_period="year",
                description_snippet="Java",
                job_url="https://careers.americanexpress.com/en/sites/CX_1/job/2",
                canonical_url="https://careers.americanexpress.com/en/sites/CX_1/job/2",
            ),
        ),
    )
    assert len(dedupe_within_run([a, b])) == 2


@pytest.mark.parametrize("status", ["SURFACED", "SCOUTED", "DISMISSED"])
def test_oracle_cross_run_suppression(session: Session, profile, status):
    run = DiscoveryRun(status=DiscoveryRunStatus.COMPLETED.value)
    session.add(run)
    session.flush()
    prior = DiscoveryResult(
        discovery_run_id=run.id,
        provider="oracle",
        external_id="old",
        source_name="American Express",
        title="Software Engineer II",
        company="American Express",
        location="Phoenix, AZ",
        job_url="https://careers.americanexpress.com/en/sites/CX_1/job/26011927",
        canonical_url="https://careers.americanexpress.com/en/sites/CX_1/job/26011927",
        discovery_score=70,
        status=status,
    )
    session.add(prior)
    session.flush()
    raw = RawDiscoveryResult(
        provider="oracle",
        source_name="American Express",
        external_id="new",
        title="Software Engineer II",
        company="American Express",
        location_text="Phoenix, AZ",
        job_url="https://careers.americanexpress.com/en/sites/CX_1/job/26011927",
        canonical_url="https://careers.americanexpress.com/en/sites/CX_1/job/26011927",
    )
    found = find_prior_identity(session, raw)
    assert found is not None
    assert should_block_resurface(found) is True


def test_oracle_description_reaches_scout_bridge(session: Session, monkeypatch, profile):
    settings = _settings()
    raw = RawDiscoveryResult(
        provider="oracle",
        source_name="American Express",
        external_id="26011927",
        title="Backend Software Engineer",
        company="American Express",
        location_text="Phoenix, AZ",
        work_arrangement="hybrid",
        salary_min=130000,
        salary_max=160000,
        salary_period="year",
        description_snippet="Java backend platform services",
        description_full=(
            "Java Spring Boot REST APIs for payments platform services. "
            "Own backend microservices, SQL data access, AWS deployments, "
            "Kubernetes operations, CI/CD pipelines, and on-call support for "
            "enterprise card and servicing systems at American Express."
        ),
        job_url="https://careers.americanexpress.com/en/sites/CX_1/job/26011927",
        canonical_url="https://careers.americanexpress.com/en/sites/CX_1/job/26011927",
    )

    class OneShot:
        name = "oracle"

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
        lambda *a, **k: (_ for _ in ()).throw(
            IngestionError("blocked", code="FETCH_BLOCKED")
        ),
    )
    outcome = scout_discovery_result(session, row.id, settings=settings)
    session.commit()
    assert outcome.ok
    assert outcome.used_structured_content is True
    assert session.query(Approval).count() == 0
    assert session.query(ApplicationPipeline).count() == 0
    assert session.query(SubmissionAuthorization).count() == 0
    assert ApprovalService(session).can_prepare_application(outcome.job.id) is False


def test_factory_includes_oracle_when_enabled():
    settings = _settings(
        discovery_provider="auto",
        discovery_oracle_enabled=True,
        discovery_greenhouse_enabled=False,
        discovery_lever_enabled=False,
        discovery_ashby_enabled=False,
        discovery_workday_enabled=False,
        discovery_remotive_enabled=False,
        discovery_enable_remotive=False,
        discovery_muse_enabled=False,
        discovery_adzuna_enabled=False,
    )
    providers = build_discovery_providers(settings)
    assert any(p.name == "oracle" for p in providers)


def test_oracle_partial_when_mixed_with_success(session: Session):
    class Boom:
        name = "oracle"

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


def test_all_provider_failure_unchanged(session: Session):
    class Boom:
        name = "oracle"

        def search(self, query):  # noqa: ANN001
            raise RuntimeError("down")

    settings = _settings()
    agent = DiscoveryAgent(session, settings=settings, providers=[Boom()])
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    assert result.status == DiscoveryRunStatus.FAILED.value
    assert result.success is False

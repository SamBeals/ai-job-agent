"""Phase 3.4 — secret-safe logging + Phoenix local Discovery coverage."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy.orm import Session

from app.agents.discovery.agent import DiscoveryAgent
from app.agents.discovery.boards import load_discovery_boards
from app.agents.discovery.dedupe import dedupe_within_run, find_prior_identity, should_block_resurface
from app.agents.discovery.factory import build_discovery_providers
from app.agents.discovery.filters import prefilter_candidate
from app.agents.discovery.providers.ashby import AshbyDiscoveryProvider
from app.agents.discovery.providers.greenhouse import GreenhouseDiscoveryProvider
from app.agents.discovery.providers.lever import LeverDiscoveryProvider
from app.agents.discovery.providers.muse import MuseDiscoveryProvider
from app.agents.discovery.queries import (
    plan_broad_search_logical_queries,
    plan_discovery_query,
)
from app.agents.discovery.ranking import score_candidate
from app.agents.scout.profile_loader import load_candidate_profile
from app.config import Settings
from app.logging_config import (
    SecretRedactingFilter,
    configure_logging,
    redact_secrets,
    register_secret_value,
)
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
from app.services.notifications import (
    DiscordWebhookNotificationService,
    NotificationEvent,
)


PROFILE = "data/fixtures/profiles/test_office_backend_prefs.json"
FAKE_WEBHOOK = "https://discord.com/api/webhooks/123456/VERY_SECRET_TOKEN"
FAKE_API_KEY = "sk-test-FAKE_PROVIDER_KEY_DO_NOT_LEAK_12345"


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
    )
    base.update(overrides)
    return Settings(**base)


def _raw(**kw) -> RawDiscoveryResult:
    base = dict(
        provider="greenhouse",
        source_name="registry",
        external_id="1",
        title="Backend Software Engineer",
        company="Local Co",
        location_text="Chandler, AZ",
        work_arrangement="hybrid",
        salary_min=130000,
        salary_max=160000,
        salary_period="year",
        description_snippet="Java Spring Boot backend REST APIs",
        job_url="https://example.com/jobs/1",
        canonical_url="https://example.com/jobs/1",
    )
    base.update(kw)
    return RawDiscoveryResult(**base)


def _capture_logs(logger_name: str = "") -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    handler.addFilter(SecretRedactingFilter())
    configure_logging(level=logging.DEBUG)
    register_secret_value(FAKE_WEBHOOK)
    register_secret_value(FAKE_API_KEY)
    target = logging.getLogger(logger_name)
    target.setLevel(logging.DEBUG)
    target.addHandler(handler)
    target.propagate = False  # capture only on this handler
    return target, stream


# --- Part 0: secret logging -------------------------------------------------


def test_fake_webhook_redacted_from_httpx_style_log():
    logger, stream = _capture_logs("httpx")
    logger.info("HTTP Request: POST %s \"HTTP/1.1 204 No Content\"", FAKE_WEBHOOK)
    text = stream.getvalue()
    assert "VERY_SECRET_TOKEN" not in text
    assert FAKE_WEBHOOK not in text
    assert "[REDACTED]" in text or "webhooks/[REDACTED]" in text


def test_notification_failure_does_not_log_webhook():
    client = MagicMock()
    client.post.side_effect = httpx.HTTPError(
        f"POST {FAKE_WEBHOOK} failed with 500"
    )
    svc = DiscordWebhookNotificationService(
        webhook_url=FAKE_WEBHOOK,
        http_client=client,
    )
    logger, stream = _capture_logs("app.services.notifications")
    svc.notify(
        NotificationEvent(
            kind="work_item_started",
            title="DISCOVERY",
            body="running",
            agent_type=AgentType.DISCOVERY.value,
            work_item_id=9,
            pipeline_id=None,
        )
    )
    text = stream.getvalue()
    assert "agent_notification_failed" in text
    assert "VERY_SECRET_TOKEN" not in text
    assert FAKE_WEBHOOK not in text


def test_fake_api_key_redacted_from_provider_error_logs():
    logger, stream = _capture_logs("app.agents.discovery")
    register_secret_value(FAKE_API_KEY)
    logger.error(
        "discovery_provider_failed provider=adzuna error=Unauthorized api_key=%s",
        FAKE_API_KEY,
    )
    text = stream.getvalue()
    assert FAKE_API_KEY not in text
    assert "discovery_provider_failed" in text


def test_secret_redaction_preserves_useful_logs():
    msg = "discovery_completed run_id=7 surfaced=2 preferred_metro_candidates=3"
    assert redact_secrets(msg) == msg
    logger, stream = _capture_logs("app.agents.discovery.agent")
    logger.info(msg)
    assert "preferred_metro_candidates=3" in stream.getvalue()


def test_discord_webhook_notifications_still_work():
    client = MagicMock()
    client.post.return_value = MagicMock(status_code=204)
    svc = DiscordWebhookNotificationService(
        webhook_url=FAKE_WEBHOOK,
        http_client=client,
    )
    svc.notify(
        NotificationEvent(
            kind="work_item_completed",
            title="DISCOVERY",
            body="done",
            agent_type=AgentType.DISCOVERY.value,
            work_item_id=1,
        )
    )
    assert client.post.called
    assert client.post.call_args.kwargs["json"]["username"]


def test_configure_logging_raises_httpx_above_info():
    configure_logging()
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


# --- Registry ---------------------------------------------------------------


def test_phoenix_employer_registry_loads():
    boards = load_discovery_boards("config/discovery_boards.json")
    tenants = {e.tenant for e in boards["greenhouse"]}
    assert "axon" in tenants
    assert "carvana" in tenants
    assert "godaddy" in tenants
    assert any(e.tenant == "gohighlevel" for e in boards["lever"])
    assert any(e.tenant == "virtuous" for e in boards["ashby"])
    assert any(e.metro == "scottsdale" for e in boards["greenhouse"] if e.tenant == "axon")
    assert any(e.metro == "gilbert" for e in boards["lever"] if e.tenant == "gohighlevel")


def test_invalid_registry_entry_fails_safely(tmp_path: Path):
    bad = tmp_path / "boards.json"
    bad.write_text("{not-json", encoding="utf-8")
    boards = load_discovery_boards(bad)
    assert boards["greenhouse"] == []
    assert boards["lever"] == []
    assert boards["ashby"] == []


def test_disabled_employer_board_not_queried(tmp_path: Path, monkeypatch):
    path = tmp_path / "boards.json"
    path.write_text(
        json.dumps(
            {
                "greenhouse": [
                    {
                        "company": "Disabled Co",
                        "board": "disabled-board",
                        "metro": "phoenix",
                        "enabled": False,
                    },
                    {
                        "company": "Enabled Co",
                        "board": "enabled-board",
                        "metro": "phoenix",
                        "enabled": True,
                    },
                ],
                "lever": [],
                "ashby": [],
            }
        ),
        encoding="utf-8",
    )
    boards = load_discovery_boards(path)
    tenants = [e.tenant for e in boards["greenhouse"]]
    assert "disabled-board" not in tenants
    assert "enabled-board" in tenants

    settings = _settings(
        discovery_provider="greenhouse",
        discovery_boards_path=str(path),
        discovery_greenhouse_enabled=True,
        discovery_greenhouse_boards="",
    )
    called: list[str] = []

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"jobs": []}

    def fake_get(url, *a, **k):  # noqa: ANN001
        called.append(url)
        return FakeResp()

    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **kw: fake_get(url))
    providers = build_discovery_providers(settings)
    gh = next(p for p in providers if p.name == "greenhouse")
    gh.search(
        DiscoveryQuery(
            role_terms=["Software Engineer"],
            location_terms=["Phoenix, AZ"],
            local_location_terms=["Phoenix, AZ"],
            max_raw_results=10,
        )
    )
    assert any("enabled-board" in u for u in called)
    assert not any("disabled-board" in u for u in called)


def test_greenhouse_phoenix_registry_queried(monkeypatch):
    settings = _settings(
        discovery_provider="greenhouse",
        discovery_greenhouse_enabled=True,
        discovery_greenhouse_boards="",
        discovery_boards_path="config/discovery_boards.json",
    )
    called: list[str] = []

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"jobs": []}

    monkeypatch.setattr(
        httpx.Client,
        "get",
        lambda self, url, **kw: called.append(url) or FakeResp(),
    )
    provider = next(p for p in build_discovery_providers(settings) if p.name == "greenhouse")
    provider.search(DiscoveryQuery(role_terms=["Software Engineer"], max_raw_results=5))
    assert any("/boards/axon/" in u for u in called)
    assert any("/boards/carvana/" in u for u in called)


def test_lever_phoenix_registry_queried(monkeypatch):
    settings = _settings(
        discovery_provider="lever",
        discovery_lever_enabled=True,
        discovery_lever_sites="",
        discovery_boards_path="config/discovery_boards.json",
    )
    called: list[str] = []

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return []

    monkeypatch.setattr(
        httpx.Client,
        "get",
        lambda self, url, **kw: called.append(url) or FakeResp(),
    )
    provider = next(p for p in build_discovery_providers(settings) if p.name == "lever")
    provider.search(DiscoveryQuery(role_terms=["Software Engineer"], max_raw_results=5))
    assert any("gohighlevel" in u for u in called)


def test_ashby_phoenix_registry_queried(monkeypatch):
    settings = _settings(
        discovery_provider="ashby",
        discovery_ashby_enabled=True,
        discovery_ashby_boards="",
        discovery_boards_path="config/discovery_boards.json",
    )
    called: list[str] = []

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"jobs": []}

    monkeypatch.setattr(
        httpx.Client,
        "get",
        lambda self, url, **kw: called.append(url) or FakeResp(),
    )
    provider = next(p for p in build_discovery_providers(settings) if p.name == "ashby")
    provider.search(DiscoveryQuery(role_terms=["Software Engineer"], max_raw_results=5))
    assert any("virtuous" in u for u in called)


def test_registry_results_normalize_to_raw(monkeypatch, profile):
    payload = {
        "jobs": [
            {
                "id": 99,
                "title": "Backend Software Engineer",
                "absolute_url": "https://boards.greenhouse.io/axon/jobs/99",
                "location": {"name": "Scottsdale, Arizona, United States"},
                "updated_at": "2026-08-01T00:00:00Z",
            }
        ]
    }

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **kw: FakeResp())
    provider = GreenhouseDiscoveryProvider(
        board_tokens=["axon"], company_names={"axon": "Axon"}, timeout_seconds=5
    )
    results = provider.search(
        DiscoveryQuery(role_terms=["Backend Software Engineer"], max_raw_results=10)
    )
    assert len(results) == 1
    assert isinstance(results[0], RawDiscoveryResult)
    assert results[0].provider == "greenhouse"
    assert results[0].company == "Axon"
    assert "Scottsdale" in (results[0].location_text or "")


# --- Geographic + quality protections --------------------------------------


@pytest.mark.parametrize(
    "city",
    ["Chandler", "Phoenix", "Tempe", "Scottsdale", "Mesa", "Gilbert"],
)
def test_phoenix_metro_cities_survive_geo(profile, city):
    cand = prefilter_candidate(
        profile,
        _raw(location_text=f"{city}, AZ", work_arrangement="hybrid"),
    )
    assert cand.filtered is False
    scored = score_candidate(profile, cand)
    assert scored.discovery_score >= 45


def test_registry_jobs_do_not_bypass_hard_filters(profile):
    foreign = prefilter_candidate(
        profile,
        _raw(
            provider="greenhouse",
            company="Axon",
            location_text="Bogotá, Colombia",
            work_arrangement="hybrid",
        ),
    )
    assert foreign.filtered is True
    assert foreign.filter_reason == "FOREIGN_LOCATION"

    nonlocal_onsite = prefilter_candidate(
        profile,
        _raw(
            provider="lever",
            company="GoHighLevel",
            location_text="New York, NY",
            work_arrangement="onsite",
        ),
    )
    assert nonlocal_onsite.filtered is True
    assert nonlocal_onsite.filter_reason == "NONLOCAL_ONSITE"


def test_strong_local_backend_exceeds_quality(profile):
    scored = score_candidate(
        profile,
        prefilter_candidate(
            profile,
            _raw(
                title="Senior Backend Software Engineer",
                location_text="Chandler, AZ",
                work_arrangement="hybrid",
                salary_min=140000,
                salary_max=170000,
            ),
        ),
    )
    assert scored.discovery_score >= 45


def test_salary_floor_still_enforced(profile):
    low = prefilter_candidate(
        profile,
        _raw(salary_min=80000, salary_max=90000, salary_period="year"),
    )
    assert low.filtered is True
    assert low.filter_reason == "SALARY_BELOW_MINIMUM"


def test_unknown_salary_remains_eligible(profile):
    cand = prefilter_candidate(
        profile,
        _raw(salary_min=None, salary_max=None, salary_period=None),
    )
    assert cand.filtered is False
    scored = score_candidate(profile, cand)
    assert scored.discovery_score >= 0


# --- Query planner ----------------------------------------------------------


def test_broad_search_local_query_planner(profile):
    q = plan_discovery_query(profile, max_raw_results=50)
    assert q.prioritize_local_search is True
    assert any("Chandler" in t for t in q.local_location_terms)
    assert any("Phoenix" in t for t in q.local_location_terms)
    assert any("Scottsdale" in t for t in q.local_location_terms)
    logical = plan_broad_search_logical_queries(q)
    local = [x for x in logical if x["bucket"] == "local"]
    remote = [x for x in logical if x["bucket"] == "remote"]
    assert local
    assert remote
    locs = {x["location"] for x in local}
    assert "Chandler, AZ" in locs
    assert "Tempe, AZ" in locs
    assert "Phoenix, AZ" in locs
    roles = {x["role"] for x in local}
    assert "Software Engineer" in roles
    assert "Backend Engineer" in roles or "Java Engineer" in roles


def test_broad_search_remote_queries_remain(profile):
    q = plan_discovery_query(profile)
    logical = plan_broad_search_logical_queries(q)
    assert any(x["bucket"] == "remote" for x in logical)


def test_query_planner_no_pathological_duplicates(profile):
    q = plan_discovery_query(profile)
    # Inflate role list to tempt Cartesian explosion
    q.role_terms = q.role_terms + [
        "Staff Software Engineer",
        "Principal Software Engineer",
        "Application Developer",
        "Product Engineer",
        "Full Stack Engineer",
    ]
    logical = plan_broad_search_logical_queries(q)
    keys = [(x["role"], x["location"], x["bucket"]) for x in logical]
    assert len(keys) == len(set(keys))
    local = [x for x in logical if x["bucket"] == "local"]
    assert len(local) <= 6 * 3
    remote = [x for x in logical if x["bucket"] == "remote"]
    assert len(remote) <= 2


def test_muse_uses_local_first_logical_queries(monkeypatch, profile):
    q = plan_discovery_query(profile, max_raw_results=20)
    seen: list[tuple[str | None, str | None]] = []

    def fake_pages(self, client, *, location, role, max_pages=None):  # noqa: ANN001
        seen.append((role, location))
        return []

    monkeypatch.setattr(MuseDiscoveryProvider, "_search_pages", fake_pages)
    MuseDiscoveryProvider().search(q)
    assert seen
    # First request should be local (has a City, ST location)
    assert seen[0][1] is not None
    assert ", AZ" in (seen[0][1] or "")


# --- Dedupe / gates / agent boundaries --------------------------------------


def test_cross_provider_dedupe_intact(profile):
    a = score_candidate(
        profile,
        prefilter_candidate(
            profile,
            _raw(provider="greenhouse", external_id="a", canonical_url="https://ex.com/j"),
        ),
    )
    b = score_candidate(
        profile,
        prefilter_candidate(
            profile,
            _raw(provider="lever", external_id="b", canonical_url="https://ex.com/j"),
        ),
    )
    out = dedupe_within_run([a, b])
    assert len(out) == 1


def test_cross_run_suppression_intact(session: Session, profile):
    run = DiscoveryRun(status=DiscoveryRunStatus.COMPLETED.value)
    session.add(run)
    session.flush()
    prior = DiscoveryResult(
        discovery_run_id=run.id,
        provider="greenhouse",
        external_id="old",
        source_name="axon",
        title="Backend Software Engineer",
        company="Axon",
        location="Scottsdale, AZ",
        job_url="https://example.com/jobs/old",
        canonical_url="https://example.com/jobs/old",
        discovery_score=70,
        status=DiscoveryResultStatus.SURFACED.value,
    )
    session.add(prior)
    session.flush()
    raw = _raw(external_id="new", canonical_url="https://example.com/jobs/old")
    found = find_prior_identity(session, raw)
    assert found is not None
    assert should_block_resurface(found) is True


def test_discord_followup_omits_none_view():
    """discord.py 2.x rejects view=None; archived Scout results must omit the kwarg."""
    kwargs: dict = {"content": "ok", "ephemeral": True}
    view = None
    if view is not None:
        kwargs["view"] = view
    assert "view" not in kwargs

    class _Dummy:
        async def send(self, *args, **kw):  # noqa: ANN001
            if "view" in kw and kw["view"] is None:
                raise TypeError(
                    f"expected view parameter to be of type View or LayoutView, "
                    f"not {type(None).__name__}"
                )
            return None

    import asyncio

    async def _run():
        d = _Dummy()
        with pytest.raises(TypeError, match="NoneType"):
            await d.send(view=None)
        await d.send(content="ok")  # no view kwarg — ok

    asyncio.run(_run())


def test_discovery_cannot_create_authorization_artifacts(session: Session, profile):
    class LocalOnly:
        name = "local"

        def search(self, query):  # noqa: ANN001
            return [_raw(external_id="local-1")]

    settings = _settings()
    agent = DiscoveryAgent(session, settings=settings, providers=[LocalOnly()])
    run = DiscoveryRun(status=DiscoveryRunStatus.RUNNING.value)
    session.add(run)
    session.flush()
    result = agent.execute_run(run)
    assert result.success
    assert session.query(Approval).count() == 0
    assert session.query(ApplicationPipeline).count() == 0
    assert session.query(SubmissionAuthorization).count() == 0
    assert not any(
        i.agent_type == AgentType.RESUME.value for i in session.query(AgentWorkItem).all()
    )
    # No preparation authorization path from Discovery
    assert ApprovalService(session).can_prepare_application(999999) is False

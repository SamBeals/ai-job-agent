"""Phase 3.6A — Multi-channel Discord control room routing."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.discord.agent_identities import AGENT_IDENTITIES, get_agent_identity
from app.discord.channel_router import (
    AgentActivitySemantic,
    DiscordChannelRouter,
    DiscordLogicalChannel,
)
from app.discord.embeds import scout_decision_embed
from app.logging_config import configure_logging, redact_secrets, register_secret_value
from app.schemas.agents import AgentType
from app.schemas.evaluation import Confidence, Recommendation, ScoutEvaluation
from app.services.notifications import (
    DiscordWebhookNotificationService,
    NullNotificationService,
    NotificationEvent,
    WEBHOOK_EVENT_KINDS,
    build_notification_service,
    settings_public_dict,
)
from tests.conftest import make_job


FAKE_LEGACY = "https://discord.com/api/webhooks/111/LEGACY_SECRET_TOKEN"
FAKE_DISCOVERY = "https://discord.com/api/webhooks/222/DISCOVERY_SECRET_TOKEN"
FAKE_SCOUT = "https://discord.com/api/webhooks/333/SCOUT_SECRET_TOKEN"
FAKE_RESUME = "https://discord.com/api/webhooks/444/RESUME_SECRET_TOKEN"
FAKE_APPS = "https://discord.com/api/webhooks/555/APPS_SECRET_TOKEN"


def _router(**kw) -> DiscordChannelRouter:
    base = dict(
        control_channel_id="100",
        discovery_channel_id="200",
        scout_channel_id="300",
        resume_channel_id="400",
        applications_channel_id="500",
        legacy_channel_id="999",
        discovery_webhook_url=FAKE_DISCOVERY,
        scout_webhook_url=FAKE_SCOUT,
        resume_webhook_url=FAKE_RESUME,
        applications_webhook_url=FAKE_APPS,
        legacy_webhook_url=FAKE_LEGACY,
    )
    base.update(kw)
    return DiscordChannelRouter(**base)


def test_logical_channels_resolve_to_configured_ids():
    r = _router()
    assert r.resolve_channel_id(DiscordLogicalChannel.CONTROL)[0] == "100"
    assert r.resolve_channel_id(DiscordLogicalChannel.DISCOVERY)[0] == "200"
    assert r.resolve_channel_id(DiscordLogicalChannel.SCOUT)[0] == "300"
    assert r.resolve_channel_id(DiscordLogicalChannel.RESUME)[0] == "400"
    assert r.resolve_channel_id(DiscordLogicalChannel.APPLICATIONS)[0] == "500"


def test_missing_specialized_falls_back_to_control():
    r = _router(discovery_channel_id="", scout_channel_id="")
    cid, reason = r.resolve_channel_id(DiscordLogicalChannel.DISCOVERY)
    assert cid == "100"
    assert reason == "control_fallback"


def test_missing_control_falls_back_to_legacy():
    r = _router(
        control_channel_id="",
        discovery_channel_id="",
        legacy_channel_id="999",
    )
    cid, reason = r.resolve_channel_id(DiscordLogicalChannel.DISCOVERY)
    assert cid == "999"
    assert reason == "legacy_fallback"


def test_missing_all_channel_config_unresolved():
    r = DiscordChannelRouter()
    cid, reason = r.resolve_channel_id(DiscordLogicalChannel.DISCOVERY)
    assert cid is None
    assert reason == "unresolved"


@pytest.mark.parametrize(
    "semantic,expected",
    [
        (AgentActivitySemantic.DISCOVERY_STARTED, DiscordLogicalChannel.DISCOVERY),
        (AgentActivitySemantic.DISCOVERY_RESULT, DiscordLogicalChannel.DISCOVERY),
        (AgentActivitySemantic.DISCOVERY_COMPLETED, DiscordLogicalChannel.DISCOVERY),
        (AgentActivitySemantic.DISCOVERY_FAILED, DiscordLogicalChannel.DISCOVERY),
        (AgentActivitySemantic.SCOUT_COMPLETED, DiscordLogicalChannel.SCOUT),
        (AgentActivitySemantic.SCOUT_APPROVAL_REQUIRED, DiscordLogicalChannel.CONTROL),
        (AgentActivitySemantic.APPROVAL_REQUIRED, DiscordLogicalChannel.CONTROL),
        (AgentActivitySemantic.RESUME_STARTED, DiscordLogicalChannel.RESUME),
        (AgentActivitySemantic.RESUME_COMPLETED, DiscordLogicalChannel.RESUME),
        (AgentActivitySemantic.RESUME_FAILED, DiscordLogicalChannel.RESUME),
        (AgentActivitySemantic.PIPELINE_BLOCKED, DiscordLogicalChannel.CONTROL),
        (AgentActivitySemantic.APPLICANT_STARTED, DiscordLogicalChannel.APPLICATIONS),
        (AgentActivitySemantic.TRACKER_UPDATE, DiscordLogicalChannel.APPLICATIONS),
    ],
)
def test_semantic_routes(semantic, expected):
    r = _router()
    assert r.route_semantic(semantic.value) == expected


def test_agent_defaults_for_applicant_tracker_resume_review():
    r = _router()
    assert r.route_agent_type("APPLICANT") == DiscordLogicalChannel.APPLICATIONS
    assert r.route_agent_type("TRACKER") == DiscordLogicalChannel.APPLICATIONS
    assert r.route_agent_type("RESUME_REVIEW") == DiscordLogicalChannel.RESUME


def test_identity_mapping_remains_centralized():
    for agent in AgentType:
        identity = get_agent_identity(agent)
        assert identity.agent_type == agent
        assert AGENT_IDENTITIES[agent].username == identity.display_name


def test_discovery_lifecycle_routes_to_discovery_webhook():
    client = MagicMock()
    client.post.return_value = SimpleNamespace(status_code=204)
    svc = DiscordWebhookNotificationService(router=_router(), http_client=client)
    for semantic in (
        "DISCOVERY_STARTED",
        "DISCOVERY_COMPLETED",
        "DISCOVERY_FAILED",
    ):
        client.reset_mock()
        svc.notify(
            NotificationEvent(
                kind="work_item_started"
                if "STARTED" in semantic
                else (
                    "work_item_failed"
                    if "FAILED" in semantic
                    else "work_item_completed"
                ),
                title="x",
                body="y",
                agent_type=AgentType.DISCOVERY.value,
                semantic_type=semantic,
                metadata={"embeds": [{"title": "t"}], "semantic_type": semantic},
            )
        )
        assert client.post.called
        assert client.post.call_args.args[0] == FAKE_DISCOVERY


def test_resume_lifecycle_routes_to_resume_webhook():
    client = MagicMock()
    client.post.return_value = SimpleNamespace(status_code=204)
    svc = DiscordWebhookNotificationService(router=_router(), http_client=client)
    svc.notify(
        NotificationEvent(
            kind="work_item_completed",
            title="x",
            body="y",
            agent_type=AgentType.RESUME.value,
            semantic_type="RESUME_COMPLETED",
            metadata={"embeds": [{"title": "t"}]},
        )
    )
    assert client.post.call_args.args[0] == FAKE_RESUME


def test_specialized_webhook_preferred_over_legacy():
    client = MagicMock()
    client.post.return_value = SimpleNamespace(status_code=204)
    svc = DiscordWebhookNotificationService(router=_router(), http_client=client)
    svc.notify(
        NotificationEvent(
            kind="work_item_started",
            title="x",
            body="y",
            agent_type=AgentType.DISCOVERY.value,
            semantic_type="DISCOVERY_STARTED",
            metadata={"embeds": [{"title": "t"}]},
        )
    )
    assert client.post.call_args.args[0] == FAKE_DISCOVERY
    assert client.post.call_args.args[0] != FAKE_LEGACY


def test_legacy_webhook_fallback_when_specialized_missing():
    client = MagicMock()
    client.post.return_value = SimpleNamespace(status_code=204)
    router = _router(discovery_webhook_url="", scout_webhook_url="", resume_webhook_url="")
    svc = DiscordWebhookNotificationService(router=router, http_client=client)
    svc.notify(
        NotificationEvent(
            kind="work_item_started",
            title="x",
            body="y",
            agent_type=AgentType.DISCOVERY.value,
            semantic_type="DISCOVERY_STARTED",
            metadata={"embeds": [{"title": "t"}]},
        )
    )
    assert client.post.call_args.args[0] == FAKE_LEGACY


def test_missing_webhook_does_not_crash():
    router = DiscordChannelRouter(
        discovery_channel_id="200",
        legacy_webhook_url="",
    )
    svc = DiscordWebhookNotificationService(router=router, http_client=MagicMock())
    svc.notify(
        NotificationEvent(
            kind="work_item_started",
            title="x",
            body="y",
            agent_type=AgentType.DISCOVERY.value,
            semantic_type="DISCOVERY_STARTED",
            metadata={"embeds": [{"title": "t"}]},
        )
    )


def test_webhook_failure_swallowed():
    client = MagicMock()
    client.post.side_effect = RuntimeError("network")
    svc = DiscordWebhookNotificationService(router=_router(), http_client=client)
    svc.notify(
        NotificationEvent(
            kind="work_item_started",
            title="x",
            body="y",
            agent_type=AgentType.RESUME.value,
            semantic_type="RESUME_STARTED",
            metadata={"embeds": [{"title": "t"}]},
        )
    )


def test_factory_null_without_any_webhook():
    settings = Settings(
        discord_agent_webhook_url="",
        discord_discovery_webhook_url="",
        discord_scout_webhook_url="",
        discord_resume_webhook_url="",
        discord_applications_webhook_url="",
    )
    assert isinstance(build_notification_service(settings), NullNotificationService)


def test_factory_uses_router_when_specialized_configured():
    settings = Settings(discord_discovery_webhook_url=FAKE_DISCOVERY)
    svc = build_notification_service(settings)
    assert isinstance(svc, DiscordWebhookNotificationService)
    assert svc._router is not None


def test_settings_public_dict_redacts_all_webhooks():
    settings = Settings(
        discord_agent_webhook_url=FAKE_LEGACY,
        discord_discovery_webhook_url=FAKE_DISCOVERY,
        discord_scout_webhook_url=FAKE_SCOUT,
        discord_resume_webhook_url=FAKE_RESUME,
        discord_applications_webhook_url=FAKE_APPS,
        discord_bot_token="BOT.TOKEN.VALUE_HERE_LONG_ENOUGH",
    )
    public = settings_public_dict(settings)
    assert public["discord_agent_webhook_url"] == "***REDACTED***"
    assert public["discord_discovery_webhook_url"] == "***REDACTED***"
    assert public["discord_scout_webhook_url"] == "***REDACTED***"
    assert public["discord_resume_webhook_url"] == "***REDACTED***"
    assert public["discord_applications_webhook_url"] == "***REDACTED***"
    assert public["discord_bot_token"] == "***REDACTED***"


def test_webhook_urls_redacted_in_logs(caplog):
    configure_logging()
    register_secret_value(FAKE_DISCOVERY)
    with caplog.at_level(logging.ERROR):
        logging.getLogger("test_control_room").error(
            "failed posting %s", FAKE_DISCOVERY
        )
    text = " ".join(r.message for r in caplog.records)
    assert FAKE_DISCOVERY not in text
    assert "[REDACTED]" in redact_secrets(FAKE_DISCOVERY) or "webhooks/[REDACTED]" in redact_secrets(
        FAKE_DISCOVERY
    )


def test_payload_rejects_embedded_webhook_secret():
    client = MagicMock()
    svc = DiscordWebhookNotificationService(router=_router(), http_client=client)
    svc.notify(
        NotificationEvent(
            kind="work_item_started",
            title="x",
            body="y",
            agent_type=AgentType.DISCOVERY.value,
            semantic_type="DISCOVERY_STARTED",
            metadata={"embeds": [{"title": FAKE_DISCOVERY}]},
        )
    )
    assert not client.post.called


def test_notification_event_is_primitive_dto():
    event = NotificationEvent(
        kind="work_item_started",
        title="t",
        body="b",
        job_id=1,
        pipeline_id=2,
        work_item_id=3,
        agent_type="DISCOVERY",
        semantic_type="DISCOVERY_STARTED",
        metadata={"embeds": [], "status": "RUNNING"},
    )
    assert event.job_id == 1
    assert "session" not in event.metadata


def test_scout_decision_embed_compact(session, job_service):
    job = make_job(job_service, company="GitHub", title="Software Engineer")
    session.commit()
    evaluation = ScoutEvaluation(
        qualification_score=78,
        desirability_score=91,
        confidence=Confidence.HIGH,
        recommendation=Recommendation.RECOMMEND,
        matching_skills=["Java"],
        missing_required_skills=[],
        missing_preferred_skills=[],
        experience_matches=[],
        partial_matches=[],
        qualification_reasoning=["Strong backend fit"],
        desirability_reasoning=["Preferred metro"],
        uncertainties=[],
        evaluator_provider="mock",
        evaluator_model="mock",
        prompt_version="v1",
    )
    embed = scout_decision_embed(
        job,
        evaluation,
        scout_channel_mention="<#300>",
        resume_channel_mention="<#400>",
    )
    assert embed.title == "Decision required"
    assert "78" in embed.fields[0].value
    assert "91" in embed.fields[1].value


def test_channel_mention_only_for_specialized():
    r = _router(discovery_channel_id="")
    # Falls back to control for resolve, but mention requires specialized
    assert r.channel_mention(DiscordLogicalChannel.DISCOVERY) is None
    assert r.channel_mention(DiscordLogicalChannel.SCOUT) == "<#300>"


def test_unimplemented_agents_have_routes_but_no_forced_activity():
    """Applicant/Tracker/Reviewer map correctly; emitting activity is not automatic."""
    r = _router()
    assert r.route_semantic("APPLICANT_STARTED") == DiscordLogicalChannel.APPLICATIONS
    assert r.route_semantic("TRACKER_UPDATE") == DiscordLogicalChannel.APPLICATIONS
    assert r.route_semantic("RESUME_REVIEW_STARTED") == DiscordLogicalChannel.RESUME
    # No webhook kinds invent Applicant activity
    assert "applicant_started" not in WEBHOOK_EVENT_KINDS


def test_agents_do_not_import_channel_ids():
    """Sanity: Discovery/Resume agent modules must not reference Discord channel settings."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "agents"
    offenders: list[str] = []
    needles = (
        "discord_discovery_channel_id",
        "discord_control_channel_id",
        "DISCORD_DISCOVERY_CHANNEL_ID",
        "DISCORD_CONTROL_CHANNEL_ID",
    )
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle in text:
                offenders.append(f"{path}:{needle}")
    assert offenders == []

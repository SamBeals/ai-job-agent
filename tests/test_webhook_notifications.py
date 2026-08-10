"""Phase 3.1 — Discord webhook agent identity notifications."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.discord.agent_activity import (
    build_agent_webhook_payload,
    resume_completed_embeds,
    resume_failed_embeds,
    resume_started_embeds,
)
from app.discord.agent_identities import AGENT_IDENTITIES, get_agent_identity
from app.models.job import JobStatus
from app.schemas.agents import AgentType, WorkItemStatus
from app.schemas.evaluation import Confidence, Recommendation, ScoutEvaluation
from app.schemas.resume_plan import ResumePlan, ResumePlanItem
from app.services.approval_service import ApprovalService
from app.services.job_service import JobService
from app.services.notifications import (
    DiscordWebhookNotificationService,
    NullNotificationService,
    NotificationEvent,
    WEBHOOK_EVENT_KINDS,
    build_notification_service,
    settings_public_dict,
)
from app.services.pipeline_orchestrator import PipelineOrchestrator
from app.services.scout_evaluation_service import ScoutEvaluationService
from app.services.work_item_service import WorkItemService
from app.agents.resume.agent import ResumeAgent
from tests.conftest import make_job

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "data" / "fixtures" / "profiles" / "test_remote_required.json"

FAKE_WEBHOOK = "https://discord.com/api/webhooks/000/TEST_SECRET_TOKEN_XYZ"


def test_all_agent_identities_exist() -> None:
    for agent in AgentType:
        identity = get_agent_identity(agent)
        assert identity.agent_type == agent
        assert identity.display_name
        assert identity.emoji
        assert AGENT_IDENTITIES[agent].username == identity.display_name


def test_factory_uses_webhook_when_configured() -> None:
    settings = Settings(discord_agent_webhook_url=FAKE_WEBHOOK)
    svc = build_notification_service(settings)
    assert isinstance(svc, DiscordWebhookNotificationService)


def test_factory_null_without_webhook() -> None:
    settings = Settings(discord_agent_webhook_url="")
    svc = build_notification_service(settings)
    assert isinstance(svc, NullNotificationService)


def test_missing_avatar_does_not_block_delivery() -> None:
    client = MagicMock()
    client.post.return_value = SimpleNamespace(status_code=204)
    svc = DiscordWebhookNotificationService(
        webhook_url=FAKE_WEBHOOK,
        avatar_urls={},
        http_client=client,
    )
    svc.notify(
        NotificationEvent(
            kind="work_item_started",
            title="RESUME AGENT",
            body="running",
            agent_type=AgentType.RESUME.value,
            work_item_id=1,
            pipeline_id=1,
            metadata={"embeds": resume_started_embeds(
                company="Co", title="Eng", pipeline_id=1, work_item_id=1
            )},
        )
    )
    assert client.post.called
    payload = client.post.call_args.kwargs["json"]
    assert "avatar_url" not in payload
    assert payload["username"] == "Resume Agent"


def test_configured_avatar_passed() -> None:
    client = MagicMock()
    client.post.return_value = SimpleNamespace(status_code=204)
    avatar = "https://cdn.example.com/resume.png"
    svc = DiscordWebhookNotificationService(
        webhook_url=FAKE_WEBHOOK,
        avatar_urls={AgentType.RESUME.value: avatar},
        http_client=client,
    )
    svc.notify(
        NotificationEvent(
            kind="work_item_started",
            title="x",
            body="y",
            agent_type=AgentType.RESUME.value,
            metadata={"embeds": [{"title": "t"}]},
        )
    )
    assert client.post.call_args.kwargs["json"]["avatar_url"] == avatar


def test_resume_events_use_resume_identity() -> None:
    for kind, embeds in [
        ("work_item_started", resume_started_embeds(
            company="GitHub", title="Software Engineer", pipeline_id=1, work_item_id=2
        )),
        ("work_item_completed", resume_completed_embeds(
            company="GitHub",
            title="Software Engineer",
            pipeline_status="RESUME_PLAN_READY",
            job_id=9,
            plan=ResumePlan(
                job_id=9,
                pipeline_id=1,
                target_title="Software Engineer",
                priority_skills=[
                    ResumePlanItem(text="Java", evidence_strength="PROFESSIONAL_EXPERIENCE")
                ],
                skills_not_to_claim=["Kafka", "Go"],
            ),
        )),
        ("work_item_failed", resume_failed_embeds(
            company="GitHub",
            title="Software Engineer",
            pipeline_status="FAILED",
            pipeline_id=1,
        )),
    ]:
        payload = build_agent_webhook_payload(
            NotificationEvent(
                kind=kind,
                title="t",
                body="b",
                agent_type=AgentType.RESUME.value,
                metadata={"embeds": embeds},
            )
        )
        assert payload["username"] == "Resume Agent"
        assert "📝" in payload["embeds"][0]["title"]


def test_complete_uses_plan_data_not_hardcoded() -> None:
    plan = ResumePlan(
        job_id=1,
        pipeline_id=1,
        target_title="SE",
        priority_skills=[
            ResumePlanItem(text="ZigZagLang", evidence_strength="PROFESSIONAL_EXPERIENCE"),
        ],
        skills_not_to_claim=["Unobtanium"],
    )
    embeds = resume_completed_embeds(
        company="Acme",
        title="SE",
        pipeline_status="RESUME_PLAN_READY",
        job_id=1,
        plan=plan,
    )
    blob = json.dumps(embeds)
    assert "ZigZagLang" in blob
    assert "Unobtanium" in blob
    assert "Kafka" not in blob  # must not hardcode


def test_no_evidence_not_in_emphasis() -> None:
    plan = ResumePlan(
        job_id=1,
        pipeline_id=1,
        target_title="SE",
        priority_skills=[
            ResumePlanItem(text="Kafka", evidence_strength="NO_EVIDENCE"),
            ResumePlanItem(text="Java", evidence_strength="PROFESSIONAL_EXPERIENCE"),
        ],
        skills_not_to_claim=["Kafka"],
    )
    embeds = resume_completed_embeds(
        company="Acme", title="SE", pipeline_status="READY", job_id=1, plan=plan
    )
    emphasis = embeds[0]["fields"][2]["value"]
    assert "Java" in emphasis
    assert "Kafka" not in emphasis


def test_webhook_url_never_in_discord_content() -> None:
    client = MagicMock()
    client.post.return_value = SimpleNamespace(status_code=204)
    svc = DiscordWebhookNotificationService(
        webhook_url=FAKE_WEBHOOK, http_client=client
    )
    svc.notify(
        NotificationEvent(
            kind="work_item_started",
            title="t",
            body="b",
            agent_type=AgentType.RESUME.value,
            metadata={"embeds": [{"title": "ok", "description": "safe"}]},
        )
    )
    payload = client.post.call_args.kwargs["json"]
    assert FAKE_WEBHOOK not in json.dumps(payload)


def test_settings_public_dict_redacts_webhook() -> None:
    settings = Settings(
        discord_agent_webhook_url=FAKE_WEBHOOK,
        discord_bot_token="bot-secret",
        openai_api_key="sk-test",
    )
    public = settings_public_dict(settings)
    assert public["discord_agent_webhook_url"] == "***REDACTED***"
    assert FAKE_WEBHOOK not in json.dumps(public)
    assert public["discord_bot_token"] == "***REDACTED***"


def test_non_lifecycle_events_not_posted() -> None:
    client = MagicMock()
    svc = DiscordWebhookNotificationService(
        webhook_url=FAKE_WEBHOOK, http_client=client
    )
    svc.notify(
        NotificationEvent(
            kind="pipeline_created",
            title="prep",
            body="queued",
            agent_type=AgentType.RESUME.value,
        )
    )
    assert not client.post.called
    assert "pipeline_created" not in WEBHOOK_EVENT_KINDS


def test_webhook_failure_does_not_fail_resume_plan(
    session, job_service: JobService, approval_service: ApprovalService
) -> None:
    class BoomClient:
        def post(self, *args, **kwargs):
            raise RuntimeError("discord unavailable")

    job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL, company="GitHub")
    ScoutEvaluationService(session).save_evaluation(
        job.id,
        ScoutEvaluation(
            job_id=job.id,
            qualification_score=80,
            desirability_score=80,
            recommendation=Recommendation.RECOMMEND,
            confidence=Confidence.MEDIUM,
            requirement_matches=[
                {
                    "requirement": {
                        "id": "1",
                        "name": "Java",
                        "category": "SKILL",
                        "requirement_type": "REQUIRED",
                    },
                    "match_level": "STRONG_MATCH",
                    "evidence_strength": "PROFESSIONAL_EXPERIENCE",
                    "candidate_evidence": ["Java"],
                    "reasoning": "pro",
                },
                {
                    "requirement": {
                        "id": "2",
                        "name": "Kafka",
                        "category": "SKILL",
                        "requirement_type": "PREFERRED",
                    },
                    "match_level": "NO_EVIDENCE",
                    "evidence_strength": "UNKNOWN",
                    "candidate_evidence": [],
                    "reasoning": "none",
                },
            ],
        ),
    )
    approval_service.approve_job(job.id, approved_by="Sam (1)")
    boom = DiscordWebhookNotificationService(
        webhook_url=FAKE_WEBHOOK, http_client=BoomClient()
    )
    orch = PipelineOrchestrator(session, notifications=boom)
    orch.on_job_preparation_approved(job.id)
    item = WorkItemService(session).claim_next(worker_id="w")
    assert item is not None
    orch.on_work_item_started(item.id)
    result = ResumeAgent(
        session, candidate_profile_path=str(PROFILE), orchestrator=orch
    ).process_work_item(item)
    session.flush()
    assert result.success is True
    assert result.resume_plan_id is not None
    session.refresh(item)
    assert item.status == WorkItemStatus.COMPLETED.value
    pipeline = orch.get_pipeline_for_job(job.id)
    assert pipeline is not None
    assert pipeline.status == "RESUME_PLAN_READY"
    assert approval_service.can_submit_application(pipeline.id) is False


def test_running_notification_requires_running_state(
    session, job_service: JobService, approval_service: ApprovalService
) -> None:
    from app.services.notifications import RecordingNotificationService

    job = make_job(job_service, status=JobStatus.AWAITING_APPROVAL)
    ScoutEvaluationService(session).save_evaluation(
        job.id,
        ScoutEvaluation(
            job_id=job.id,
            qualification_score=70,
            desirability_score=70,
            recommendation=Recommendation.RECOMMEND,
            confidence=Confidence.MEDIUM,
        ),
    )
    approval_service.approve_job(job.id, approved_by="Sam (1)")
    recorder = RecordingNotificationService()
    orch = PipelineOrchestrator(session, notifications=recorder)
    orch.on_job_preparation_approved(job.id)
    item = WorkItemService(session).claim_next(worker_id="w")
    assert item is not None
    assert item.status == WorkItemStatus.RUNNING.value
    orch.on_work_item_started(item.id)
    started = [e for e in recorder.events if e.kind == "work_item_started"]
    assert len(started) == 1
    assert started[0].metadata.get("status") == "RUNNING"
    assert started[0].agent_type == AgentType.RESUME.value


def test_no_notifications_for_unimplemented_agents() -> None:
    """Identity exists for future agents; webhook kinds are only for real work events."""
    client = MagicMock()
    svc = DiscordWebhookNotificationService(
        webhook_url=FAKE_WEBHOOK, http_client=client
    )
    for agent in (
        AgentType.DISCOVERY,
        AgentType.TRACKER,
        AgentType.APPLICANT,
        AgentType.RESUME_REVIEW,
    ):
        svc.notify(
            NotificationEvent(
                kind="fake_thinking",
                title="noise",
                body="should not send",
                agent_type=agent.value,
            )
        )
    assert not client.post.called

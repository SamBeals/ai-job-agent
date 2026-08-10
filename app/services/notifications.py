"""Notification abstraction — agents never import Discord / call webhooks directly."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Lifecycle kinds posted to the agent activity webhook (truthful work events only).
WEBHOOK_EVENT_KINDS = frozenset(
    {
        "work_item_started",
        "work_item_completed",
        "work_item_failed",
        "pipeline_blocked",
    }
)


@dataclass
class NotificationEvent:
    """Structured notification payload (not free-form agent chat)."""

    kind: str
    title: str
    body: str
    job_id: int | None = None
    pipeline_id: int | None = None
    work_item_id: int | None = None
    agent_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class NotificationService(Protocol):
    def notify(self, event: NotificationEvent) -> None:
        """Deliver a notification. Must not raise into callers' business transactions."""
        ...


class NullNotificationService:
    """No-op notifier when Discord activity feed is disabled."""

    def __init__(self, *, reason: str = "no_webhook") -> None:
        self.reason = reason
        logger.info("agent_notifications_disabled reason=%s", reason)

    def notify(self, event: NotificationEvent) -> None:
        logger.info(
            "agent_notification_skipped kind=%s agent=%s work_item_id=%s pipeline_id=%s",
            event.kind,
            event.agent_type,
            event.work_item_id,
            event.pipeline_id,
        )


class RecordingNotificationService:
    """In-memory notifier for tests."""

    def __init__(self) -> None:
        self.events: list[NotificationEvent] = []
        self.payloads: list[dict[str, Any]] = []

    def notify(self, event: NotificationEvent) -> None:
        self.events.append(event)


class LoggingNotificationService:
    """Logs notifications without contacting Discord."""

    def notify(self, event: NotificationEvent) -> None:
        logger.info(
            "agent_notification kind=%s agent=%s job_id=%s pipeline_id=%s work_item_id=%s",
            event.kind,
            event.agent_type,
            event.job_id,
            event.pipeline_id,
            event.work_item_id,
        )


class DiscordWebhookNotificationService:
    """Post agent activity via a Discord channel webhook with per-agent identity.

    Failures are logged and swallowed — they must never corrupt pipeline state.
    The webhook URL is treated as a secret and must never appear in logs/content.
    """

    def __init__(
        self,
        *,
        webhook_url: str,
        avatar_urls: dict[str, str] | None = None,
        timeout_seconds: float = 5.0,
        http_client: Any | None = None,
    ) -> None:
        if not webhook_url:
            raise ValueError("webhook_url is required")
        self._webhook_url = webhook_url
        self._avatar_urls = {k: v for k, v in (avatar_urls or {}).items() if v}
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client  # injectable for tests

    def notify(self, event: NotificationEvent) -> None:
        if event.kind not in WEBHOOK_EVENT_KINDS:
            logger.info(
                "agent_notification_skipped kind=%s reason=not_activity_lifecycle",
                event.kind,
            )
            return
        try:
            from app.discord.agent_activity import build_agent_webhook_payload
            from app.discord.agent_identities import get_agent_identity

            identity = get_agent_identity(event.agent_type)
            avatar = self._avatar_urls.get(identity.agent_type.value)
            payload = build_agent_webhook_payload(
                event,
                identity=identity,
                avatar_url=avatar or None,
            )
            # Safety: never allow webhook URL into outbound content
            blob = str(payload)
            if self._webhook_url in blob:
                logger.error(
                    "agent_notification_failed agent=%s work_item_id=%s "
                    "reason=payload_contained_secret",
                    event.agent_type,
                    event.work_item_id,
                )
                return
            self._post(payload)
            logger.info(
                "agent_notification_sent kind=%s agent=%s work_item_id=%s pipeline_id=%s",
                event.kind,
                event.agent_type,
                event.work_item_id,
                event.pipeline_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "agent_notification_failed kind=%s agent=%s work_item_id=%s pipeline_id=%s",
                event.kind,
                event.agent_type,
                event.work_item_id,
                event.pipeline_id,
            )

    def _post(self, payload: dict[str, Any]) -> None:
        import httpx

        client = self._http_client
        if client is not None:
            response = client.post(
                self._webhook_url,
                json=payload,
                timeout=self._timeout_seconds,
            )
            if getattr(response, "status_code", 200) >= 400:
                raise RuntimeError(
                    f"webhook HTTP {getattr(response, 'status_code', '?')}"
                )
            return

        with httpx.Client(timeout=self._timeout_seconds) as http:
            response = http.post(self._webhook_url, json=payload)
            if response.status_code >= 400:
                raise RuntimeError(f"webhook HTTP {response.status_code}")


def _avatar_map_from_settings(settings: Any) -> dict[str, str]:
    from app.schemas.agents import AgentType

    mapping = {
        AgentType.SCOUT.value: getattr(settings, "scout_avatar_url", "") or "",
        AgentType.RESUME.value: getattr(settings, "resume_avatar_url", "") or "",
        AgentType.RESUME_REVIEW.value: getattr(settings, "resume_review_avatar_url", "") or "",
        AgentType.APPLICANT.value: getattr(settings, "applicant_avatar_url", "") or "",
        AgentType.DISCOVERY.value: getattr(settings, "discovery_avatar_url", "") or "",
        AgentType.TRACKER.value: getattr(settings, "tracker_avatar_url", "") or "",
    }
    return {k: v for k, v in mapping.items() if v}


def build_notification_service(
    settings: Any | None = None,
    *,
    bot_token: str = "",
    channel_id: str = "",
    webhook_url: str | None = None,
) -> NotificationService:
    """Construct the process-wide notification backend.

    Prefer DISCORD_AGENT_WEBHOOK_URL (per-agent webhook identities).
    Legacy bot_token+channel_id args are ignored when a webhook is configured.
    Missing webhook → NullNotificationService (pipeline still works).
    """
    if settings is None and webhook_url is None:
        # Backward-compatible call sites may pass only bot_token/channel_id
        if not bot_token and not channel_id:
            from app.config import get_settings

            settings = get_settings()

    url = webhook_url
    avatar_map: dict[str, str] = {}
    timeout = 5.0
    if settings is not None:
        url = url or (getattr(settings, "discord_agent_webhook_url", "") or "")
        avatar_map = _avatar_map_from_settings(settings)
        timeout = float(getattr(settings, "discord_webhook_timeout_seconds", 5.0) or 5.0)

    if url:
        return DiscordWebhookNotificationService(
            webhook_url=url,
            avatar_urls=avatar_map,
            timeout_seconds=timeout,
        )

    return NullNotificationService(reason="no_DISCORD_AGENT_WEBHOOK_URL")


def settings_public_dict(settings: Any) -> dict[str, Any]:
    """Settings snapshot safe for logging/status — secrets redacted."""
    data = settings.model_dump() if hasattr(settings, "model_dump") else dict(settings)
    secret_keys = {
        "discord_bot_token",
        "discord_agent_webhook_url",
        "openai_api_key",
        "discovery_api_key",
    }
    redacted = {}
    for key, value in data.items():
        if key in secret_keys and value:
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted

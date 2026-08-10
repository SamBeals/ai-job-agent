"""Notification abstraction — agents never import Discord directly."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


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
    """No-op notifier for tests / missing Discord config."""

    def notify(self, event: NotificationEvent) -> None:
        logger.info(
            "notification_skipped kind=%s job_id=%s pipeline_id=%s",
            event.kind,
            event.job_id,
            event.pipeline_id,
        )


class RecordingNotificationService:
    """In-memory notifier for tests."""

    def __init__(self) -> None:
        self.events: list[NotificationEvent] = []

    def notify(self, event: NotificationEvent) -> None:
        self.events.append(event)


class LoggingNotificationService:
    """Logs notifications; used when Discord channel is unavailable."""

    def notify(self, event: NotificationEvent) -> None:
        logger.info(
            "agent_notification kind=%s title=%s job_id=%s pipeline_id=%s",
            event.kind,
            event.title,
            event.job_id,
            event.pipeline_id,
        )


class DiscordNotificationService:
    """Best-effort Discord channel notifications.

    Failures are logged and swallowed — they must never corrupt pipeline state.
    Requires a bot token + channel id; sends via HTTP REST to avoid coupling
    workers to a live discord.py gateway connection.
    """

    def __init__(
        self,
        *,
        bot_token: str,
        channel_id: str,
    ) -> None:
        self.bot_token = bot_token
        self.channel_id = channel_id

    def notify(self, event: NotificationEvent) -> None:
        if not self.bot_token or not self.channel_id:
            logger.warning(
                "Discord notification skipped — DISCORD_BOT_TOKEN or "
                "DISCORD_CHANNEL_ID not configured (kind=%s)",
                event.kind,
            )
            return
        try:
            import httpx

            content = f"**{event.title}**\n{event.body}"
            if len(content) > 1900:
                content = content[:1899] + "…"
            url = f"https://discord.com/api/v10/channels/{self.channel_id}/messages"
            headers = {
                "Authorization": f"Bot {self.bot_token}",
                "Content-Type": "application/json",
            }
            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, headers=headers, json={"content": content})
                if response.status_code >= 400:
                    logger.warning(
                        "Discord notification failed status=%s kind=%s",
                        response.status_code,
                        event.kind,
                    )
        except Exception:  # noqa: BLE001
            logger.exception("Discord notification error (non-fatal) kind=%s", event.kind)


def build_notification_service(
    *,
    bot_token: str = "",
    channel_id: str = "",
) -> NotificationService:
    if bot_token and channel_id:
        return DiscordNotificationService(bot_token=bot_token, channel_id=channel_id)
    return LoggingNotificationService()

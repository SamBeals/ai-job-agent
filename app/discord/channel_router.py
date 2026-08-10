"""Centralized Discord channel routing — agents never see raw channel IDs.

Logical destinations (CONTROL / DISCOVERY / SCOUT / RESUME / APPLICATIONS) are
resolved from configuration. Identity ("who speaks") stays in agent_identities;
routing ("where it goes") lives here only.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class DiscordLogicalChannel(str, Enum):
    CONTROL = "control"
    DISCOVERY = "discovery"
    SCOUT = "scout"
    RESUME = "resume"
    APPLICATIONS = "applications"


class AgentActivitySemantic(str, Enum):
    """Semantic activity labels for routing (primitives only — not ORM)."""

    DISCOVERY_STARTED = "DISCOVERY_STARTED"
    DISCOVERY_RESULT = "DISCOVERY_RESULT"
    DISCOVERY_COMPLETED = "DISCOVERY_COMPLETED"
    DISCOVERY_FAILED = "DISCOVERY_FAILED"

    SCOUT_STARTED = "SCOUT_STARTED"
    SCOUT_COMPLETED = "SCOUT_COMPLETED"
    SCOUT_FAILED = "SCOUT_FAILED"
    SCOUT_APPROVAL_REQUIRED = "SCOUT_APPROVAL_REQUIRED"

    RESUME_STARTED = "RESUME_STARTED"
    RESUME_COMPLETED = "RESUME_COMPLETED"
    RESUME_FAILED = "RESUME_FAILED"
    RESUME_REVIEW_STARTED = "RESUME_REVIEW_STARTED"
    RESUME_REVIEW_COMPLETED = "RESUME_REVIEW_COMPLETED"

    APPLICANT_STARTED = "APPLICANT_STARTED"
    APPLICANT_COMPLETED = "APPLICANT_COMPLETED"
    TRACKER_UPDATE = "TRACKER_UPDATE"

    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    REJECTION_CONFIRMATION = "REJECTION_CONFIRMATION"
    PIPELINE_REQUIRES_USER = "PIPELINE_REQUIRES_USER"
    PIPELINE_BLOCKED = "PIPELINE_BLOCKED"
    SUBMISSION_AUTHORIZATION_REQUIRED = "SUBMISSION_AUTHORIZATION_REQUIRED"


# Single source of truth: semantic → logical channel
_SEMANTIC_ROUTES: dict[str, DiscordLogicalChannel] = {
    AgentActivitySemantic.DISCOVERY_STARTED.value: DiscordLogicalChannel.DISCOVERY,
    AgentActivitySemantic.DISCOVERY_RESULT.value: DiscordLogicalChannel.DISCOVERY,
    AgentActivitySemantic.DISCOVERY_COMPLETED.value: DiscordLogicalChannel.DISCOVERY,
    AgentActivitySemantic.DISCOVERY_FAILED.value: DiscordLogicalChannel.DISCOVERY,
    AgentActivitySemantic.SCOUT_STARTED.value: DiscordLogicalChannel.SCOUT,
    AgentActivitySemantic.SCOUT_COMPLETED.value: DiscordLogicalChannel.SCOUT,
    AgentActivitySemantic.SCOUT_FAILED.value: DiscordLogicalChannel.SCOUT,
    AgentActivitySemantic.SCOUT_APPROVAL_REQUIRED.value: DiscordLogicalChannel.CONTROL,
    AgentActivitySemantic.RESUME_STARTED.value: DiscordLogicalChannel.RESUME,
    AgentActivitySemantic.RESUME_COMPLETED.value: DiscordLogicalChannel.RESUME,
    AgentActivitySemantic.RESUME_FAILED.value: DiscordLogicalChannel.RESUME,
    AgentActivitySemantic.RESUME_REVIEW_STARTED.value: DiscordLogicalChannel.RESUME,
    AgentActivitySemantic.RESUME_REVIEW_COMPLETED.value: DiscordLogicalChannel.RESUME,
    AgentActivitySemantic.APPLICANT_STARTED.value: DiscordLogicalChannel.APPLICATIONS,
    AgentActivitySemantic.APPLICANT_COMPLETED.value: DiscordLogicalChannel.APPLICATIONS,
    AgentActivitySemantic.TRACKER_UPDATE.value: DiscordLogicalChannel.APPLICATIONS,
    AgentActivitySemantic.APPROVAL_REQUIRED.value: DiscordLogicalChannel.CONTROL,
    AgentActivitySemantic.REJECTION_CONFIRMATION.value: DiscordLogicalChannel.CONTROL,
    AgentActivitySemantic.PIPELINE_REQUIRES_USER.value: DiscordLogicalChannel.CONTROL,
    AgentActivitySemantic.PIPELINE_BLOCKED.value: DiscordLogicalChannel.CONTROL,
    AgentActivitySemantic.SUBMISSION_AUTHORIZATION_REQUIRED.value: DiscordLogicalChannel.CONTROL,
}

# Agent type defaults when semantic is absent (lifecycle webhook events)
_AGENT_DEFAULT_CHANNEL: dict[str, DiscordLogicalChannel] = {
    "DISCOVERY": DiscordLogicalChannel.DISCOVERY,
    "SCOUT": DiscordLogicalChannel.SCOUT,
    "RESUME": DiscordLogicalChannel.RESUME,
    "RESUME_REVIEW": DiscordLogicalChannel.RESUME,
    "APPLICANT": DiscordLogicalChannel.APPLICATIONS,
    "TRACKER": DiscordLogicalChannel.APPLICATIONS,
}


class DiscordChannelRouter:
    """Map semantic activity → logical channel → configured Discord snowflake / webhook.

    Fallback for channel IDs:
      specialized → CONTROL → legacy DISCORD_CHANNEL_ID → None

    Fallback for webhooks:
      specialized → legacy DISCORD_AGENT_WEBHOOK_URL → None

    Legacy webhook destination is wherever that webhook was created in Discord —
    it cannot retarget to a specialized channel. Callers must not pretend otherwise.
    """

    def __init__(
        self,
        *,
        control_channel_id: str = "",
        discovery_channel_id: str = "",
        scout_channel_id: str = "",
        resume_channel_id: str = "",
        applications_channel_id: str = "",
        legacy_channel_id: str = "",
        discovery_webhook_url: str = "",
        scout_webhook_url: str = "",
        resume_webhook_url: str = "",
        applications_webhook_url: str = "",
        legacy_webhook_url: str = "",
    ) -> None:
        self._channel_ids = {
            DiscordLogicalChannel.CONTROL: (control_channel_id or "").strip(),
            DiscordLogicalChannel.DISCOVERY: (discovery_channel_id or "").strip(),
            DiscordLogicalChannel.SCOUT: (scout_channel_id or "").strip(),
            DiscordLogicalChannel.RESUME: (resume_channel_id or "").strip(),
            DiscordLogicalChannel.APPLICATIONS: (applications_channel_id or "").strip(),
        }
        self._legacy_channel_id = (legacy_channel_id or "").strip()
        self._webhook_urls = {
            DiscordLogicalChannel.DISCOVERY: (discovery_webhook_url or "").strip(),
            DiscordLogicalChannel.SCOUT: (scout_webhook_url or "").strip(),
            DiscordLogicalChannel.RESUME: (resume_webhook_url or "").strip(),
            DiscordLogicalChannel.APPLICATIONS: (applications_webhook_url or "").strip(),
            # Control is bot-owned; no dedicated control webhook by default
            DiscordLogicalChannel.CONTROL: "",
        }
        self._legacy_webhook_url = (legacy_webhook_url or "").strip()

    @classmethod
    def from_settings(cls, settings: Any) -> DiscordChannelRouter:
        return cls(
            control_channel_id=getattr(settings, "discord_control_channel_id", "") or "",
            discovery_channel_id=getattr(settings, "discord_discovery_channel_id", "") or "",
            scout_channel_id=getattr(settings, "discord_scout_channel_id", "") or "",
            resume_channel_id=getattr(settings, "discord_resume_channel_id", "") or "",
            applications_channel_id=getattr(
                settings, "discord_applications_channel_id", ""
            )
            or "",
            legacy_channel_id=getattr(settings, "discord_channel_id", "") or "",
            discovery_webhook_url=getattr(
                settings, "discord_discovery_webhook_url", ""
            )
            or "",
            scout_webhook_url=getattr(settings, "discord_scout_webhook_url", "") or "",
            resume_webhook_url=getattr(settings, "discord_resume_webhook_url", "") or "",
            applications_webhook_url=getattr(
                settings, "discord_applications_webhook_url", ""
            )
            or "",
            legacy_webhook_url=getattr(settings, "discord_agent_webhook_url", "") or "",
        )

    def route_semantic(self, semantic_type: str | None) -> DiscordLogicalChannel | None:
        if not semantic_type:
            return None
        return _SEMANTIC_ROUTES.get(str(semantic_type).strip().upper())

    def route_agent_type(self, agent_type: str | None) -> DiscordLogicalChannel | None:
        if not agent_type:
            return None
        return _AGENT_DEFAULT_CHANNEL.get(str(agent_type).strip().upper())

    def route_event(self, event: Any) -> DiscordLogicalChannel:
        """Resolve logical channel for a NotificationEvent-like object."""
        meta = getattr(event, "metadata", None) or {}
        if isinstance(meta, dict):
            explicit = meta.get("logical_channel")
            if explicit:
                try:
                    return DiscordLogicalChannel(str(explicit).lower())
                except ValueError:
                    pass
            semantic = meta.get("semantic_type") or getattr(event, "semantic_type", None)
        else:
            semantic = getattr(event, "semantic_type", None)

        routed = self.route_semantic(semantic)
        if routed is not None:
            return routed

        agent_routed = self.route_agent_type(getattr(event, "agent_type", None))
        if agent_routed is not None:
            return agent_routed

        # Safe default: control plane (human-visible), not applications noise
        return DiscordLogicalChannel.CONTROL

    def resolve_channel_id(
        self, logical: DiscordLogicalChannel
    ) -> tuple[str | None, str]:
        """Return (channel_id, resolution_reason).

        reason: specialized | control_fallback | legacy_fallback | unresolved
        """
        specialized = self._channel_ids.get(logical) or ""
        if specialized and specialized.isdigit():
            return specialized, "specialized"

        if logical != DiscordLogicalChannel.CONTROL:
            control = self._channel_ids.get(DiscordLogicalChannel.CONTROL) or ""
            if control and control.isdigit():
                logger.info(
                    "discord_channel_fallback logical=%s reason=control_fallback",
                    logical.value,
                )
                return control, "control_fallback"

        legacy = self._legacy_channel_id
        if legacy and legacy.isdigit():
            logger.info(
                "discord_channel_fallback logical=%s reason=legacy_fallback",
                logical.value,
            )
            return legacy, "legacy_fallback"

        return None, "unresolved"

    def resolve_webhook_url(
        self, logical: DiscordLogicalChannel
    ) -> tuple[str | None, str]:
        """Return (webhook_url, resolution_reason).

        reason: specialized | legacy_fallback | unresolved

        Legacy fallback does NOT retarget the webhook to ``logical`` — the
        message lands wherever the legacy webhook was created in Discord.
        """
        specialized = self._webhook_urls.get(logical) or ""
        if specialized:
            return specialized, "specialized"

        if self._legacy_webhook_url:
            logger.info(
                "discord_channel_fallback logical=%s reason=legacy_webhook_fallback "
                "note=legacy_webhook_destination_unchanged",
                logical.value,
            )
            return self._legacy_webhook_url, "legacy_fallback"

        return None, "unresolved"

    def channel_mention(self, logical: DiscordLogicalChannel) -> str | None:
        channel_id, reason = self.resolve_channel_id(logical)
        if not channel_id or reason != "specialized":
            # Only mention when the specialized channel is truly configured
            specialized = self._channel_ids.get(logical) or ""
            if specialized and specialized.isdigit():
                return f"<#{specialized}>"
            return None
        return f"<#{channel_id}>"

    def any_webhook_configured(self) -> bool:
        if self._legacy_webhook_url:
            return True
        return any(bool(u) for u in self._webhook_urls.values())

    def all_webhook_urls(self) -> list[str]:
        urls = [u for u in self._webhook_urls.values() if u]
        if self._legacy_webhook_url:
            urls.append(self._legacy_webhook_url)
        # Dedupe preserve order
        out: list[str] = []
        seen: set[str] = set()
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

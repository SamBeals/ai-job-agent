"""Bot-plane Discord delivery helpers (channel resolve + Scout dual-post).

Interactive components stay on the real Discord bot. Webhooks never carry buttons.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from app.discord.channel_router import DiscordChannelRouter, DiscordLogicalChannel
from app.discord.embeds import scout_decision_embed, scout_evaluation_embed
from app.discord.views import JobActionView
from app.models.job import Job, JobStatus
from app.schemas.evaluation import ScoutEvaluation

logger = logging.getLogger(__name__)


async def resolve_text_channel(
    client: discord.Client,
    router: DiscordChannelRouter,
    logical: DiscordLogicalChannel,
) -> discord.abc.Messageable | None:
    """Resolve a logical channel to a Discord messageable channel, or None."""
    channel_id, reason = router.resolve_channel_id(logical)
    if not channel_id:
        logger.info(
            "discord_activity_routed logical_channel=%s reason=unresolved transport=bot",
            logical.value,
        )
        return None
    channel = client.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await client.fetch_channel(int(channel_id))
        except Exception:  # noqa: BLE001
            logger.warning(
                "discord_channel_fetch_failed logical_channel=%s reason=%s",
                logical.value,
                reason,
            )
            return None
    if not hasattr(channel, "send"):
        return None
    logger.info(
        "discord_activity_routed logical_channel=%s reason=%s transport=bot",
        logical.value,
        reason,
    )
    return channel


async def publish_scout_evaluation(
    *,
    client: discord.Client,
    router: DiscordChannelRouter,
    job: Job,
    evaluation: ScoutEvaluation,
    settings: Any | None = None,
    extraction_warnings: list[str] | None = None,
    extraction_confidence: str | None = None,
    source_note: str = "",
) -> dict[str, Any]:
    """Post full Scout eval to #scout and compact decision card to #job-control.

    Returns delivery flags for the caller (ephemeral ack text).
    Never raises into business logic for channel-send failures.
    """
    del settings  # reserved for future mention formatting
    result = {
        "scout_posted": False,
        "control_posted": False,
        "needs_approval": job.status_enum == JobStatus.AWAITING_APPROVAL,
    }

    full_embed = scout_evaluation_embed(
        job,
        evaluation,
        extraction_warnings=extraction_warnings,
        extraction_confidence=extraction_confidence,
    )
    scout_ch = await resolve_text_channel(
        client, router, DiscordLogicalChannel.SCOUT
    )
    if scout_ch is not None:
        try:
            content_parts = ["🔎 **Scout evaluation**"]
            if source_note:
                content_parts.append(source_note)
            content_parts.append(
                "Scout recommendation is **not** authorization. "
                "Only **APPROVE** authorizes this exact job."
            )
            await scout_ch.send(content="\n".join(content_parts), embed=full_embed)
            result["scout_posted"] = True
            logger.info(
                "discord_control_message_sent logical_channel=scout job_id=%s",
                job.id,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "agent_notification_failed logical_channel=scout job_id=%s", job.id
            )

    if result["needs_approval"]:
        control_ch = await resolve_text_channel(
            client, router, DiscordLogicalChannel.CONTROL
        )
        if control_ch is not None:
            try:
                scout_mention = router.channel_mention(DiscordLogicalChannel.SCOUT)
                resume_mention = router.channel_mention(DiscordLogicalChannel.RESUME)
                decision = scout_decision_embed(
                    job,
                    evaluation,
                    scout_channel_mention=scout_mention,
                    resume_channel_mention=resume_mention,
                )
                view = JobActionView(job.id, job.job_url, timeout=None)
                await control_ch.send(
                    content=(
                        "Decision required — Scout recommends reviewing this role.\n"
                        "APPROVE authorizes preparation only (Gate 1)."
                    ),
                    embed=decision,
                    view=view,
                )
                result["control_posted"] = True
                logger.info(
                    "discord_control_message_sent logical_channel=control "
                    "event=SCOUT_APPROVAL_REQUIRED job_id=%s",
                    job.id,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "agent_notification_failed logical_channel=control job_id=%s",
                    job.id,
                )

    return result


def scout_publish_ack_text(delivery: dict[str, Any]) -> str:
    parts: list[str] = []
    if delivery.get("scout_posted"):
        parts.append("Full evaluation posted to the Scout workspace.")
    if delivery.get("control_posted"):
        parts.append("Compact decision card posted to the control channel.")
    if delivery.get("needs_approval") and not delivery.get("control_posted"):
        parts.append(
            "Approval buttons could not be posted to control — "
            "use ephemeral controls below if shown."
        )
    if not parts:
        parts.append("Scout finished. Workspace channels were not configured.")
    return "\n".join(parts)

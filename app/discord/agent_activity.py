"""Reusable Discord webhook embed payloads for truthful agent activity."""

from __future__ import annotations

from typing import Any

from app.discord.agent_identities import AgentDiscordIdentity, get_agent_identity
from app.schemas.agents import AgentType
from app.schemas.resume_plan import ResumePlan
from app.services.notifications import NotificationEvent


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def build_agent_webhook_payload(
    event: NotificationEvent,
    *,
    identity: AgentDiscordIdentity | None = None,
    avatar_url: str | None = None,
) -> dict[str, Any]:
    """Build Discord webhook JSON. Never includes secrets or webhook URL."""
    identity = identity or get_agent_identity(event.agent_type)
    embeds = event.metadata.get("embeds")
    if not embeds:
        embeds = [_default_embed(event, identity)]
    payload: dict[str, Any] = {
        "username": identity.username,
        "embeds": embeds,
    }
    if avatar_url:
        payload["avatar_url"] = avatar_url
    # Optional plain content is empty — embeds carry the message
    return payload


def resume_started_embeds(
    *,
    company: str,
    title: str,
    pipeline_id: int,
    work_item_id: int,
) -> list[dict[str, Any]]:
    identity = get_agent_identity(AgentType.RESUME)
    return [
        {
            "title": f"{identity.emoji} {identity.display_name.upper()}",
            "description": f"**{company}** — {title}",
            "color": 0x5865F2,
            "fields": [
                {"name": "Task", "value": "Build tailored resume plan", "inline": True},
                {"name": "Status", "value": "RUNNING", "inline": True},
                {
                    "name": "Refs",
                    "value": f"Pipeline #{pipeline_id} · work item #{work_item_id}",
                    "inline": False,
                },
            ],
        }
    ]


def resume_completed_embeds(
    *,
    company: str,
    title: str,
    pipeline_status: str,
    job_id: int,
    plan: ResumePlan | None,
) -> list[dict[str, Any]]:
    identity = get_agent_identity(AgentType.RESUME)
    emphasis = _emphasis_lines(plan)
    avoid = _avoid_lines(plan)
    fields: list[dict[str, Any]] = [
        {"name": "Status", "value": "COMPLETE", "inline": True},
        {"name": "Pipeline", "value": pipeline_status, "inline": True},
    ]
    if emphasis:
        fields.append(
            {
                "name": "Primary emphasis",
                "value": _truncate("\n".join(emphasis), 600),
                "inline": False,
            }
        )
    if avoid:
        fields.append(
            {
                "name": "Avoiding unsupported claims",
                "value": _truncate("\n".join(avoid), 400),
                "inline": False,
            }
        )
    fields.append(
        {
            "name": "Inspect",
            "value": f"Use `/resume-plan {job_id}` for the full plan.",
            "inline": False,
        }
    )
    return [
        {
            "title": f"{identity.emoji} {identity.display_name.upper()} — COMPLETE",
            "description": (
                f"**{company}** — {title}\n"
                "Resume strategy prepared."
            ),
            "color": 0x57F287,
            "fields": fields,
        }
    ]


def resume_failed_embeds(
    *,
    company: str,
    title: str,
    pipeline_status: str,
    pipeline_id: int,
) -> list[dict[str, Any]]:
    identity = get_agent_identity(AgentType.RESUME)
    return [
        {
            "title": f"{identity.emoji} {identity.display_name.upper()} — FAILED",
            "description": (
                f"**{company}** — {title}\n"
                "I couldn't complete the resume plan."
            ),
            "color": 0xED4245,
            "fields": [
                {"name": "Pipeline status", "value": pipeline_status, "inline": True},
                {
                    "name": "Next step",
                    "value": f"Use `/pipeline-status` with pipeline `{pipeline_id}` for details.",
                    "inline": False,
                },
            ],
        }
    ]


def _emphasis_lines(plan: ResumePlan | None) -> list[str]:
    if plan is None:
        return []
    lines: list[str] = []
    for item in (plan.priority_skills or plan.requirements_to_emphasize)[:6]:
        strength = (item.evidence_strength or "").upper()
        if strength in {"NO_EVIDENCE", "UNKNOWN"} and not item.evidence_refs:
            continue
        if strength == "NO_EVIDENCE":
            continue
        detail = f" — {item.source_detail}" if item.source_detail else ""
        lines.append(f"• {item.text}{detail}")
    return lines


def _avoid_lines(plan: ResumePlan | None) -> list[str]:
    if plan is None:
        return []
    return [f"• {name}" for name in plan.skills_not_to_claim[:8]]


def _default_embed(event: NotificationEvent, identity: AgentDiscordIdentity) -> dict[str, Any]:
    return {
        "title": f"{identity.emoji} {event.title}",
        "description": _truncate(event.body, 1500),
        "color": 0x5865F2,
    }

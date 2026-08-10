"""Central Discord presentation identities for AgentType.

ONE bot = control plane. ONE webhook = activity feed with per-agent usernames.
These are presentation labels for real internal agents — not separate Discord apps.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.agents import AgentType


@dataclass(frozen=True)
class AgentDiscordIdentity:
    """Webhook username / display mapping for one AgentType."""

    agent_type: AgentType
    display_name: str
    emoji: str

    @property
    def username(self) -> str:
        """Discord webhook username override (without emoji — Discord limits)."""
        return self.display_name

    @property
    def titled(self) -> str:
        return f"{self.emoji} {self.display_name}"


AGENT_IDENTITIES: dict[AgentType, AgentDiscordIdentity] = {
    AgentType.SCOUT: AgentDiscordIdentity(
        agent_type=AgentType.SCOUT,
        display_name="Scout",
        emoji="🔎",
    ),
    AgentType.RESUME: AgentDiscordIdentity(
        agent_type=AgentType.RESUME,
        display_name="Resume Agent",
        emoji="📝",
    ),
    AgentType.RESUME_REVIEW: AgentDiscordIdentity(
        agent_type=AgentType.RESUME_REVIEW,
        display_name="Resume Reviewer",
        emoji="🔍",
    ),
    AgentType.APPLICANT: AgentDiscordIdentity(
        agent_type=AgentType.APPLICANT,
        display_name="Applicant",
        emoji="🖥",
    ),
    AgentType.DISCOVERY: AgentDiscordIdentity(
        agent_type=AgentType.DISCOVERY,
        display_name="Discovery",
        emoji="📡",
    ),
    AgentType.TRACKER: AgentDiscordIdentity(
        agent_type=AgentType.TRACKER,
        display_name="Tracker",
        emoji="📬",
    ),
}


def get_agent_identity(agent: AgentType | str | None) -> AgentDiscordIdentity:
    """Resolve AgentType → Discord identity. Defaults to Scout for unknown."""
    if agent is None:
        return AGENT_IDENTITIES[AgentType.SCOUT]
    if isinstance(agent, str):
        try:
            agent = AgentType(agent)
        except ValueError:
            return AGENT_IDENTITIES[AgentType.SCOUT]
    return AGENT_IDENTITIES.get(agent, AGENT_IDENTITIES[AgentType.SCOUT])


def avatar_env_key(agent: AgentType) -> str:
    """Settings field name for optional avatar URL."""
    return {
        AgentType.SCOUT: "scout_avatar_url",
        AgentType.RESUME: "resume_avatar_url",
        AgentType.RESUME_REVIEW: "resume_review_avatar_url",
        AgentType.APPLICANT: "applicant_avatar_url",
        AgentType.DISCOVERY: "discovery_avatar_url",
        AgentType.TRACKER: "tracker_avatar_url",
    }[agent]

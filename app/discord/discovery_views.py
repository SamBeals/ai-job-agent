"""Discord embeds and views for Discovery results."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord

from app.agents.discovery.scout_bridge import dismiss_discovery_result, scout_discovery_result
from app.config import Settings, get_settings
from app.database.database import SessionLocal
from app.discord.embeds import scout_evaluation_embed, scout_evaluation_failed_embed
from app.discord.views import JobActionView
from app.models.discovery import DiscoveryResult, DiscoveryRun
from app.models.job import JobStatus
from app.schemas.discovery import DiscoveryResultStatus

logger = logging.getLogger(__name__)

_REASON_LABELS = {
    "TARGET_SOFTWARE_ROLE": "Target software role",
    "TARGET_ROLE": "Target role",
    "BACKEND_SIGNAL": "Backend role",
    "JAVA_SIGNAL": "Java signal",
    "SPECIALIZED_ROLE": "Specialized engineering role",
    "WEAK_SOFTWARE_SIGNAL": "Weak software signal",
    "DEVELOPMENT_SIGNAL": "Development role",
    "CHANDLER": "Chandler",
    "PHOENIX_METRO": "Phoenix metro",
    "PREFERRED_LOCATION": "Preferred location",
    "LOCAL_HYBRID": "Local hybrid",
    "LOCAL_ONSITE": "Local on-site",
    "PREFERRED_METRO": "Preferred metro",
    "PREFERRED_LOCATION": "Preferred location",
    "ACCEPTABLE_METRO": "Acceptable metro",
    "NONLOCAL_ONSITE": "Nonlocal on-site",
    "NONLOCAL_HYBRID": "Nonlocal hybrid",
    "NONLOCAL_PHYSICAL_UNKNOWN": "Nonlocal physical (arrangement unknown)",
    "REMOTE_ELIGIBILITY_UNKNOWN": "Remote eligibility unknown",
    "REMOTE_REGION_RESTRICTED": "Remote region restricted",
    "FOREIGN_LOCATION": "Foreign location",
    "HYBRID": "Hybrid",
    "HYBRID_NONLOCAL": "Hybrid (outside preferred metro)",
    "HYBRID_LOCATION_UNKNOWN": "Hybrid (location unresolved)",
    "ONSITE": "On-site",
    "ONSITE_NONLOCAL": "On-site (outside preferred metro)",
    "ONSITE_LOCATION_UNKNOWN": "On-site (location unresolved)",
    "US_REMOTE": "US-eligible remote",
    "REMOTE_ACCEPTABLE": "Remote (accepted)",
    "REMOTE_LOCATION_UNKNOWN": "Remote (location unresolved)",
    "US_ELIGIBLE": "US-eligible location",
    "LOCATION_UNKNOWN": "Location unknown",
    "GEO_UNKNOWN": "Location unknown",
    "NON_SOFTWARE_DEVELOPER_CONTEXT": "Non-software developer context",
    "MANDATORY_LANGUAGE_SIGNAL": "Mandatory language signal",
    "SALARY_ABOVE_MINIMUM": "Salary above configured minimum",
    "SALARY_UNKNOWN": "Salary unknown",
    "FRESH_POSTING": "Fresh posting",
}


def discovery_result_embed(row: DiscoveryResult) -> discord.Embed:
    location = row.location or "Location unknown"
    arrangement = (row.work_arrangement or "unknown").title()
    salary = _format_salary(row)
    reasons = row.reason_codes or []
    why_lines = [
        f"✓ {_REASON_LABELS.get(code, code.replace('_', ' ').title())}"
        for code in reasons
        if code != "SALARY_UNKNOWN"
    ]
    if "SALARY_UNKNOWN" in reasons:
        why_lines.append("· Salary not listed")

    embed = discord.Embed(
        title="📡 DISCOVERY RESULT",
        description=f"**{row.title}**\n{row.company}",
        color=discord.Color.teal(),
        url=row.open_url,
    )
    embed.add_field(
        name="Location / arrangement",
        value=f"{location} · {arrangement}",
        inline=False,
    )
    embed.add_field(name="Salary", value=salary, inline=True)
    embed.add_field(
        name="Discovery score",
        value=f"{row.discovery_score}/100",
        inline=True,
    )
    if why_lines:
        embed.add_field(
            name="Why it surfaced",
            value="\n".join(why_lines[:8]),
            inline=False,
        )
    embed.add_field(
        name="Note",
        value="This job has **NOT** been fully evaluated by Scout.",
        inline=False,
    )
    embed.set_footer(text=f"discovery result #{row.id} · {row.provider}")
    return embed


def discovery_run_status_embed(run: DiscoveryRun, *, extras: dict[str, int] | None = None) -> discord.Embed:
    extras = extras or {}
    started = run.started_at.strftime("%Y-%m-%d %H:%M UTC") if run.started_at else "?"
    sources = ", ".join(run.providers_used or []) or "—"
    embed = discord.Embed(
        title=f"DISCOVERY RUN #{run.id}",
        color=discord.Color.teal(),
    )
    embed.add_field(name="Status", value=run.status, inline=True)
    embed.add_field(name="Started", value=started, inline=True)
    embed.add_field(name="Sources", value=sources[:1024], inline=False)
    embed.add_field(name="Raw results", value=str(run.raw_result_count), inline=True)
    embed.add_field(name="Passed filters", value=str(run.filtered_result_count), inline=True)
    quality = getattr(run, "quality_result_count", None)
    if quality is not None:
        embed.add_field(name="Passed quality", value=str(quality), inline=True)
    embed.add_field(name="Strong / surfaced", value=str(run.surfaced_result_count), inline=True)
    if extras:
        embed.add_field(name="Dismissed", value=str(extras.get("dismissed", 0)), inline=True)
        embed.add_field(name="Scouted", value=str(extras.get("scouted", 0)), inline=True)
    return embed


def _format_salary(row: DiscoveryResult) -> str:
    if row.salary_min is None and row.salary_max is None:
        return "Unknown"
    currency = row.salary_currency or "USD"

    def _fmt(n: int) -> str:
        if n >= 1000:
            return f"${n // 1000}k"
        return f"${n}"

    if row.salary_min is not None and row.salary_max is not None:
        return f"{_fmt(row.salary_min)}–{_fmt(row.salary_max)} {currency}"
    val = row.salary_max if row.salary_max is not None else row.salary_min
    assert val is not None
    return f"{_fmt(val)}+ {currency}"


class DiscoveryResultView(discord.ui.View):
    """Control-bot buttons for a DiscoveryResult."""

    def __init__(self, result_id: int, job_url: str | None, *, timeout: float | None = None) -> None:
        super().__init__(timeout=timeout)
        self.result_id = result_id
        view_url = job_url or "https://example.com/jobs/placeholder"
        self.add_item(
            discord.ui.Button(
                label="VIEW JOB",
                style=discord.ButtonStyle.link,
                url=view_url,
            )
        )
        scout_btn = discord.ui.Button(
            label="SCOUT THIS",
            style=discord.ButtonStyle.primary,
            custom_id=f"discovery_scout:{result_id}",
        )
        scout_btn.callback = self._scout_callback  # type: ignore[method-assign]
        self.add_item(scout_btn)

        dismiss_btn = discord.ui.Button(
            label="DISMISS",
            style=discord.ButtonStyle.secondary,
            custom_id=f"discovery_dismiss:{result_id}",
        )
        dismiss_btn.callback = self._dismiss_callback  # type: ignore[method-assign]
        self.add_item(dismiss_btn)

    async def _scout_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        settings = get_settings()
        try:
            with SessionLocal() as session:
                outcome = scout_discovery_result(
                    session, self.result_id, settings=settings
                )
                session.commit()
                if not outcome.ok:
                    await interaction.followup.send(outcome.message, ephemeral=True)
                    return
                assert outcome.job is not None and outcome.evaluation is not None
                embed = scout_evaluation_embed(outcome.job, outcome.evaluation)
                view = (
                    JobActionView(outcome.job.id, outcome.job.job_url, timeout=None)
                    if outcome.job.status_enum == JobStatus.AWAITING_APPROVAL
                    else None
                )
                if interaction.message:
                    row = session.get(DiscoveryResult, self.result_id)
                    if row:
                        await interaction.message.edit(
                            embed=discovery_result_embed(row),
                            view=self.disabled_view(self.result_id, row.open_url, label="SCOUTED"),
                        )
                await interaction.followup.send(
                    content=(
                        "**Scout evaluation** (from Discovery)\n"
                        "Scout recommendation is **not** authorization. "
                        "Only **APPROVE** authorizes this exact job."
                    ),
                    embed=embed,
                    view=view,
                    ephemeral=True,
                )
        except Exception:  # noqa: BLE001
            logger.exception("discovery scout_this failed result_id=%s", self.result_id)
            await interaction.followup.send(
                embed=scout_evaluation_failed_embed(
                    detail="Scout could not evaluate this Discovery result."
                ),
                ephemeral=True,
            )

    async def _dismiss_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            with SessionLocal() as session:
                row = dismiss_discovery_result(session, self.result_id)
                session.commit()
                if interaction.message:
                    await interaction.message.edit(
                        embed=discovery_result_embed(row),
                        view=self.disabled_view(self.result_id, row.open_url, label="DISMISSED"),
                    )
                await interaction.followup.send(
                    f"Dismissed discovery result `{self.result_id}`.",
                    ephemeral=True,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("dismiss failed: %s", exc)
            await interaction.followup.send(f"Cannot dismiss: {exc}", ephemeral=True)

    @staticmethod
    def disabled_view(
        result_id: int,
        job_url: str | None,
        *,
        label: str = "DONE",
    ) -> discord.ui.View:
        view = discord.ui.View(timeout=None)
        view.add_item(
            discord.ui.Button(
                label="VIEW JOB",
                style=discord.ButtonStyle.link,
                url=job_url or "https://example.com/jobs/placeholder",
            )
        )
        view.add_item(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                disabled=True,
                custom_id=f"discovery_done:{result_id}",
            )
        )
        return view


async def post_pending_discovery_results(bot: discord.Client, settings: Settings) -> int:
    """Post SURFACED results that have not yet been delivered to the control channel."""
    channel_id = (settings.discord_channel_id or "").strip()
    if not channel_id or not channel_id.isdigit():
        return 0
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except Exception:  # noqa: BLE001
            return 0
    if not hasattr(channel, "send"):
        return 0

    posted = 0
    with SessionLocal() as session:
        from sqlalchemy import select

        rows = list(
            session.scalars(
                select(DiscoveryResult)
                .where(
                    DiscoveryResult.status == DiscoveryResultStatus.SURFACED.value,
                    DiscoveryResult.discord_posted_at.is_(None),
                )
                .order_by(DiscoveryResult.discovery_score.desc(), DiscoveryResult.id.asc())
                .limit(10)
            ).all()
        )
        for row in rows:
            url = row.open_url
            if not url:
                continue
            embed = discovery_result_embed(row)
            view = DiscoveryResultView(row.id, url, timeout=None)
            await channel.send(embed=embed, view=view)
            row.discord_posted_at = datetime.now(timezone.utc)
            posted += 1
        session.commit()
    return posted

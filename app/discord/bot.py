"""Discord bot — primary control interface for AI Job Agent."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.database.database import SessionLocal, init_db
from app.discord.embeds import job_recommendation_embed, system_status_embed
from app.discord.scout_views import ScoutIngestView
from app.discord.views import JobActionView
from app.models.job import Job, JobStatus
from app.services.job_service import JobService

logger = logging.getLogger(__name__)


class JobAgentBot(commands.Bot):
    """Discord bot for job review and explicit approval."""

    def __init__(self, settings: Settings | None = None) -> None:
        intents = discord.Intents.default()
        intents.message_content = False
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings or get_settings()

    async def setup_hook(self) -> None:
        init_db()
        if self.settings.discord_guild_id:
            guild = discord.Object(id=int(self.settings.discord_guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Synced commands to guild %s", self.settings.discord_guild_id)
        else:
            await self.tree.sync()
            logger.info("Synced global application commands")

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")


def create_bot(settings: Settings | None = None) -> JobAgentBot:
    """Create and configure the Discord bot with slash commands."""
    settings = settings or get_settings()
    bot = JobAgentBot(settings=settings)

    @bot.tree.command(name="status", description="Show AI Job Agent system status")
    async def status_command(interaction: discord.Interaction) -> None:
        with SessionLocal() as session:
            total = session.scalar(select(func.count()).select_from(Job)) or 0
            awaiting = session.scalar(
                select(func.count()).select_from(Job).where(
                    Job.status == JobStatus.AWAITING_APPROVAL.value
                )
            ) or 0
            approved = session.scalar(
                select(func.count()).select_from(Job).where(
                    Job.status == JobStatus.APPROVED.value
                )
            ) or 0

            embed = system_status_embed(
                env=settings.app_env,
                job_count=total,
                awaiting_count=awaiting,
                approved_count=approved,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="jobs", description="List jobs currently awaiting approval")
    async def jobs_command(interaction: discord.Interaction) -> None:
        with SessionLocal() as session:
            jobs = JobService(session).list_awaiting_approval()

        if not jobs:
            await interaction.response.send_message(
                "No jobs currently awaiting approval.",
                ephemeral=True,
            )
            return

        first, rest = jobs[0], jobs[1:5]
        view = JobActionView(first.id, first.job_url, timeout=None)
        await interaction.response.send_message(
            embed=job_recommendation_embed(first),
            view=view,
            ephemeral=True,
        )
        for job in rest:
            await interaction.followup.send(
                embed=job_recommendation_embed(job),
                view=JobActionView(job.id, job.job_url, timeout=None),
                ephemeral=True,
            )
        if len(jobs) > 5:
            await interaction.followup.send(
                f"...and {len(jobs) - 5} more awaiting approval. "
                "Use the database or future pagination for the rest.",
                ephemeral=True,
            )

    @bot.tree.command(
        name="testjob",
        description="[DEV] Insert a fake job recommendation and post it",
    )
    async def testjob_command(interaction: discord.Interaction) -> None:
        if not settings.enable_test_commands and not settings.is_development:
            await interaction.response.send_message(
                "Test commands are disabled in this environment.",
                ephemeral=True,
            )
            return

        with SessionLocal() as session:
            job = JobService(session).create_fake_recommendation()
            session.commit()
            session.refresh(job)
            embed = job_recommendation_embed(job)
            view = JobActionView(job.id, job.job_url, timeout=None)

        await interaction.response.send_message(
            content=(
                "**Fake job recommendation** (dev only)\n"
                "Press **APPROVE** only if you intend to authorize this job. "
                "Conversational replies do not count as approval."
            ),
            embed=embed,
            view=view,
        )

    @bot.tree.command(
        name="scout-test",
        description="[DEV] Manually ingest a fixture, URL, or pasted job for Scout",
    )
    async def scout_test_command(interaction: discord.Interaction) -> None:
        if not settings.enable_test_commands and not settings.is_development:
            await interaction.response.send_message(
                "Test commands are disabled in this environment.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            content=(
                "**AI Job Scout — manual evaluation**\n"
                "Choose how to supply a job. Scout may recommend, but **never authorizes**.\n"
                "Only the **APPROVE** button creates authorization for that exact job.\n\n"
                "Long postings: Discord paste is capped at 4000 characters — use the CLI for full text:\n"
                "`python -m app.agents.scout.evaluate --file ./job.txt`"
            ),
            view=ScoutIngestView(settings),
            ephemeral=True,
        )

    return bot


def run_bot() -> None:
    """Entrypoint for running the Discord bot."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    settings = get_settings()
    if not settings.discord_bot_token:
        raise SystemExit(
            "DISCORD_BOT_TOKEN is not set. Copy .env.example to .env and configure it."
        )

    bot = create_bot(settings)
    bot.run(settings.discord_bot_token)


if __name__ == "__main__":
    run_bot()

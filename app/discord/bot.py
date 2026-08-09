"""Discord bot — primary control interface for AI Job Agent."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select

from app.agents.scout.pipeline import ScoutPipeline
from app.agents.scout.profile_loader import load_candidate_profile
from app.config import Settings, get_settings
from app.database.database import SessionLocal, init_db
from app.discord.embeds import (
    job_recommendation_embed,
    scout_evaluation_embed,
    system_status_embed,
)
from app.discord.views import JobActionView
from app.models.job import Job, JobStatus
from app.schemas.job_posting import NormalizedJob
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

        # Show up to 5 embeds to stay within Discord limits
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
            # Refresh attributes after commit
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
        description="[DEV] Evaluate a Scout fixture job (does not authorize)",
    )
    @app_commands.describe(
        fixture="Fixture name: a_strong_backend | b_ml_research | c_onsite | d_missing | e_keyword | f_preferred"
    )
    async def scout_test_command(interaction: discord.Interaction, fixture: str) -> None:
        if not settings.enable_test_commands and not settings.is_development:
            await interaction.response.send_message(
                "Test commands are disabled in this environment.",
                ephemeral=True,
            )
            return

        fixture_map = {
            "a_strong_backend": "fixture_a_strong_backend.json",
            "b_ml_research": "fixture_b_ml_research.json",
            "c_onsite": "fixture_c_onsite_undesirable.json",
            "d_missing": "fixture_d_missing_info.json",
            "e_keyword": "fixture_e_keyword_trap.json",
            "f_preferred": "fixture_f_preferred_gap.json",
        }
        filename = fixture_map.get(fixture.strip().lower())
        if filename is None:
            await interaction.response.send_message(
                "Unknown fixture. Choose one of: " + ", ".join(fixture_map),
                ephemeral=True,
            )
            return

        path = Path("data/fixtures/scout") / filename
        if not path.exists():
            await interaction.response.send_message(
                f"Fixture file missing: {path}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            job_data = json.loads(path.read_text(encoding="utf-8"))
            normalized = NormalizedJob.model_validate(job_data)
            # Use remote-required test profile for fixture C to demonstrate independence
            if fixture.strip().lower() == "c_onsite":
                profile = load_candidate_profile(
                    "data/fixtures/profiles/test_remote_required.json"
                )
            else:
                profile = load_candidate_profile(settings.candidate_profile_path)

            with SessionLocal() as session:
                pipeline = ScoutPipeline(settings=settings, session=session)
                result = pipeline.evaluate(
                    normalized,
                    profile,
                    persist=True,
                    create_job_record=True,
                )
                session.commit()
                if result.job is None:
                    await interaction.followup.send(
                        "Evaluation completed but no job was persisted.",
                        ephemeral=True,
                    )
                    return
                session.refresh(result.job)
                embed = scout_evaluation_embed(result.job, result.evaluation)
                view = JobActionView(result.job.id, result.job.job_url, timeout=None)
                await interaction.followup.send(
                    content=(
                        f"**Scout test:** `{fixture}`\n"
                        f"Present to user: `{result.should_present}`\n"
                        "Scout recommendation is **not** authorization. "
                        "Only APPROVE authorizes this exact job."
                    ),
                    embed=embed,
                    view=view if result.job.status_enum == JobStatus.AWAITING_APPROVAL else None,
                    ephemeral=True,
                )
        except Exception as exc:  # noqa: BLE001 — surface clear Discord error
            logger.exception("scout-test failed")
            await interaction.followup.send(
                f"Scout test failed safely: `{exc}`",
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

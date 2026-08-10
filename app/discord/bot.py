"""Discord bot — primary control interface for AI Job Agent."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.database.database import SessionLocal, init_db
from app.discord.embeds import (
    job_recommendation_embed,
    scout_detail_embed,
    system_status_embed,
)
from app.discord.scout_views import ScoutIngestView
from app.discord.views import JobActionView
from app.models.job import Job, JobStatus
from app.schemas.evaluation import ScoutEvaluation
from app.services.job_service import JobService
from app.services.scout_evaluation_service import ScoutEvaluationService

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
        if not getattr(self, "_discovery_poster_started", False):
            self._discovery_poster_started = True
            self.loop.create_task(self._discovery_result_poster())

    async def _discovery_result_poster(self) -> None:
        """Deliver SURFACED DiscoveryResult cards via the control bot (buttons need the bot)."""
        import asyncio

        await self.wait_until_ready()
        from app.discord.discovery_views import post_pending_discovery_results

        while not self.is_closed():
            try:
                await post_pending_discovery_results(self, self.settings)
            except Exception:  # noqa: BLE001
                logger.exception("discovery_result_poster_failed")
            await asyncio.sleep(3)


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

    @bot.tree.command(
        name="scout-detail",
        description="Show requirement-level Scout analysis for an evaluated job",
    )
    @app_commands.describe(job_id="Persisted job id")
    async def scout_detail_command(interaction: discord.Interaction, job_id: int) -> None:
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job is None:
                await interaction.response.send_message(
                    f"Job {job_id} not found.",
                    ephemeral=True,
                )
                return
            record = ScoutEvaluationService(session).latest_for_job(job_id)
            if record is None:
                await interaction.response.send_message(
                    f"No Scout evaluation found for job {job_id}.",
                    ephemeral=True,
                )
                return
            evaluation = ScoutEvaluation.model_validate(record.evaluation_json)
            embed = scout_detail_embed(job, evaluation)
        await interaction.response.send_message(
            content=(
                "Requirement-level Scout detail. "
                "This is **not** authorization — Approve is still required."
            ),
            embed=embed,
            ephemeral=True,
        )

    @bot.tree.command(name="pipeline", description="List active application pipelines")
    async def pipeline_command(interaction: discord.Interaction) -> None:
        from app.discord.pipeline_embeds import pipeline_list_embed
        from app.schemas.agents import AgentType, WorkItemTaskType
        from app.services.approval_service import ApprovalService
        from app.services.pipeline_orchestrator import PipelineOrchestrator
        from app.services.work_item_service import WorkItemService

        with SessionLocal() as session:
            orch = PipelineOrchestrator(session)
            approvals = ApprovalService(session)
            work = WorkItemService(session)
            rows = []
            for pipeline in orch.list_active_pipelines():
                job = session.get(Job, pipeline.job_id)
                if job is None:
                    continue
                resume = work.find_for_pipeline_task(
                    pipeline.id, AgentType.RESUME, WorkItemTaskType.BUILD_RESUME_PLAN
                )
                stages = {
                    "scout": "COMPLETE" if job.scout_evaluations else "?",
                    "prep": "YES" if approvals.can_prepare_application(job.id) else "NO",
                    "resume": (
                        "COMPLETE"
                        if resume and resume.status == "COMPLETED"
                        else (resume.status if resume else "NOT STARTED")
                    ),
                    "submit": "YES" if approvals.can_submit_application(pipeline.id) else "NO",
                }
                rows.append((pipeline, job, stages))
            embed = pipeline_list_embed(rows)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(
        name="pipeline-status",
        description="Show detailed status for a job or pipeline",
    )
    @app_commands.describe(
        job_id="Job id (optional if pipeline_id given)",
        pipeline_id="Pipeline id (optional if job_id given)",
    )
    async def pipeline_status_command(
        interaction: discord.Interaction,
        job_id: int | None = None,
        pipeline_id: int | None = None,
    ) -> None:
        from app.discord.pipeline_embeds import pipeline_status_embed
        from app.models.pipeline import ApplicationPipeline
        from app.schemas.agents import AgentType, WorkItemTaskType
        from app.services.approval_service import ApprovalService
        from app.services.pipeline_orchestrator import PipelineOrchestrator
        from app.services.work_item_service import WorkItemService

        if job_id is None and pipeline_id is None:
            await interaction.response.send_message(
                "Provide job_id or pipeline_id.",
                ephemeral=True,
            )
            return

        with SessionLocal() as session:
            orch = PipelineOrchestrator(session)
            approvals = ApprovalService(session)
            work = WorkItemService(session)
            pipeline = None
            if pipeline_id is not None:
                pipeline = session.get(ApplicationPipeline, pipeline_id)
            elif job_id is not None:
                pipeline = orch.get_pipeline_for_job(job_id)
            if pipeline is None:
                await interaction.response.send_message(
                    "Application pipeline not found.",
                    ephemeral=True,
                )
                return
            job = session.get(Job, pipeline.job_id)
            if job is None:
                await interaction.response.send_message("Job missing.", ephemeral=True)
                return
            approval = approvals.get_approval_for_job(job.id)
            scout = ScoutEvaluationService(session).latest_for_job(job.id)
            evaluation = (
                ScoutEvaluation.model_validate(scout.evaluation_json) if scout else None
            )
            resume_item = work.find_for_pipeline_task(
                pipeline.id, AgentType.RESUME, WorkItemTaskType.BUILD_RESUME_PLAN
            )
            from app.models.resume_plan import ResumePlanRecord
            from sqlalchemy import select as sa_select

            resume_plan = session.scalars(
                sa_select(ResumePlanRecord)
                .where(ResumePlanRecord.pipeline_id == pipeline.id)
                .order_by(ResumePlanRecord.id.desc())
            ).first()
            embed = pipeline_status_embed(
                pipeline=pipeline,
                job=job,
                approval=approval,
                evaluation=evaluation,
                resume_item=resume_item,
                resume_plan=resume_plan,
                can_submit=approvals.can_submit_application(pipeline.id),
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="agents", description="Show agent operational status")
    async def agents_command(interaction: discord.Interaction) -> None:
        from app.discord.pipeline_embeds import agents_status_embed
        from app.schemas.agents import AgentType
        from app.services.work_item_service import WorkItemService

        with SessionLocal() as session:
            work = WorkItemService(session)
            resume_counts = work.counts_by_agent(AgentType.RESUME)
            discovery_counts = work.counts_by_agent(AgentType.DISCOVERY)
            embed = agents_status_embed(
                resume_counts, discovery_counts=discovery_counts
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(
        name="discover",
        description="Queue Discovery Agent to search for current job opportunities",
    )
    async def discover_command(interaction: discord.Interaction) -> None:
        from app.agents.discovery.agent import DiscoveryAgentError, queue_discovery_run

        # Do NOT search here — only persist DiscoveryRun + work item.
        with SessionLocal() as session:
            try:
                run, item = queue_discovery_run(session, settings=settings)
                session.commit()
                run_id, item_id = run.id, item.id
            except DiscoveryAgentError as exc:
                await interaction.response.send_message(
                    content=f"📡 **DISCOVERY AGENT**\n{exc}",
                    ephemeral=True,
                )
                return

        await interaction.response.send_message(
            content=(
                "📡 **DISCOVERY AGENT**\n"
                "Job search queued.\n\n"
                "I'll post the strongest opportunities here when the search completes.\n"
                f"_Run #{run_id} · work item #{item_id}_"
            ),
            ephemeral=False,
        )

    @bot.tree.command(
        name="discovery-status",
        description="Show the latest Discovery run status",
    )
    async def discovery_status_command(interaction: discord.Interaction) -> None:
        from sqlalchemy import func, select

        from app.discord.discovery_views import discovery_run_status_embed
        from app.models.discovery import DiscoveryResult, DiscoveryRun
        from app.schemas.discovery import DiscoveryResultStatus

        with SessionLocal() as session:
            run = session.scalars(
                select(DiscoveryRun).order_by(DiscoveryRun.id.desc()).limit(1)
            ).first()
            if run is None:
                await interaction.response.send_message(
                    "No Discovery runs yet. Use `/discover`.",
                    ephemeral=True,
                )
                return
            dismissed = session.scalar(
                select(func.count())
                .select_from(DiscoveryResult)
                .where(DiscoveryResult.status == DiscoveryResultStatus.DISMISSED.value)
            ) or 0
            scouted = session.scalar(
                select(func.count())
                .select_from(DiscoveryResult)
                .where(
                    DiscoveryResult.status.in_(
                        [
                            DiscoveryResultStatus.SCOUT_REQUESTED.value,
                            DiscoveryResultStatus.SCOUTED.value,
                        ]
                    )
                )
            ) or 0
            embed = discovery_run_status_embed(
                run, extras={"dismissed": int(dismissed), "scouted": int(scouted)}
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(
        name="resume-plan",
        description="Show the ResumePlan for a job",
    )
    @app_commands.describe(job_id="Persisted job id")
    async def resume_plan_command(interaction: discord.Interaction, job_id: int) -> None:
        from app.discord.pipeline_embeds import resume_plan_embed
        from app.models.resume_plan import ResumePlanRecord
        from sqlalchemy import select as sa_select

        with SessionLocal() as session:
            job = session.get(Job, job_id)
            if job is None:
                await interaction.response.send_message(
                    f"Job {job_id} not found.",
                    ephemeral=True,
                )
                return
            record = session.scalars(
                sa_select(ResumePlanRecord)
                .where(ResumePlanRecord.job_id == job_id)
                .order_by(ResumePlanRecord.id.desc())
            ).first()
            if record is None:
                await interaction.response.send_message(
                    f"No ResumePlan for job {job_id} yet.",
                    ephemeral=True,
                )
                return
            embed = resume_plan_embed(job, record)
        await interaction.response.send_message(embed=embed, ephemeral=True)

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

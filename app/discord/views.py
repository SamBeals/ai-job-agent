"""Discord interactive views — Approve / Reject / View Job buttons."""

from __future__ import annotations

import logging

import discord

from app.config import get_settings
from app.database.database import SessionLocal
from app.discord.embeds import job_recommendation_embed
from app.discord.pipeline_embeds import preparation_approved_embed
from app.services.approval_service import (
    ApprovalError,
    ApprovalNotAllowedError,
    ApprovalService,
    DuplicateApprovalError,
    JobNotFoundError,
)
from app.services.notifications import build_notification_service
from app.services.pipeline_orchestrator import OrchestrationError, PipelineOrchestrator

logger = logging.getLogger(__name__)


class JobActionView(discord.ui.View):
    """Persistent-style view for a single job recommendation message.

    custom_id embeds the job_id so Discord can route interactions correctly.
    """

    def __init__(self, job_id: int, job_url: str | None, *, timeout: float | None = None) -> None:
        super().__init__(timeout=timeout)
        self.job_id = job_id

        view_url = job_url or "https://example.com/jobs/placeholder"
        self.add_item(
            discord.ui.Button(
                label="VIEW JOB",
                style=discord.ButtonStyle.link,
                url=view_url,
            )
        )

        approve_btn = discord.ui.Button(
            label="APPROVE",
            style=discord.ButtonStyle.success,
            custom_id=f"job_approve:{job_id}",
        )
        approve_btn.callback = self._approve_callback  # type: ignore[method-assign]
        self.add_item(approve_btn)

        reject_btn = discord.ui.Button(
            label="REJECT",
            style=discord.ButtonStyle.danger,
            custom_id=f"job_reject:{job_id}",
        )
        reject_btn.callback = self._reject_callback  # type: ignore[method-assign]
        self.add_item(reject_btn)

    async def _approve_callback(self, interaction: discord.Interaction) -> None:
        await self._handle_approve(interaction)

    async def _reject_callback(self, interaction: discord.Interaction) -> None:
        await self._handle_reject(interaction)

    async def _handle_approve(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        approved_by = f"{user.display_name} ({user.id})"
        settings = get_settings()

        try:
            with SessionLocal() as session:
                service = ApprovalService(session)
                result = service.approve_job(
                    self.job_id,
                    approved_by=approved_by,
                    approval_source="discord",
                    approval_action="approve",
                    discord_message_id=str(interaction.message.id) if interaction.message else None,
                    discord_user_id=str(user.id),
                )
                notifications = build_notification_service(
                    bot_token=settings.discord_bot_token,
                    channel_id=settings.discord_channel_id,
                )
                orch = PipelineOrchestrator(session, notifications=notifications)
                try:
                    orch_result = orch.on_job_preparation_approved(self.job_id)
                except OrchestrationError as exc:
                    session.commit()
                    logger.error("Orchestration failed after approve job=%s: %s", self.job_id, exc)
                    await interaction.followup.send(
                        f"Approved job `{self.job_id}`, but pipeline setup failed: {exc}",
                        ephemeral=True,
                    )
                    return

                session.commit()
                job = result.job
                pipeline = orch_result.pipeline

                embed = job_recommendation_embed(
                    job,
                    footer_note=f"Preparation approved by {approved_by}",
                )
                disabled_view = JobActionView.disabled_view(self.job_id, job.job_url)

                if interaction.message:
                    await interaction.message.edit(embed=embed, view=disabled_view)

                prep_embed = preparation_approved_embed(
                    job,
                    pipeline,
                    work_item_id=orch_result.work_item_id,
                    already=result.already_approved,
                )
                await interaction.followup.send(
                    content=(
                        "Preparation authorized. Resume Agent work has been queued. "
                        "Submission remains **locked** until a separate Gate 2 approval."
                    ),
                    embed=prep_embed,
                    ephemeral=True,
                )
        except (JobNotFoundError, ApprovalNotAllowedError, DuplicateApprovalError) as exc:
            logger.warning("Approve failed for job %s: %s", self.job_id, exc)
            await interaction.followup.send(f"Cannot approve: {exc}", ephemeral=True)
        except ApprovalError as exc:
            logger.exception("Unexpected approval error for job %s", self.job_id)
            await interaction.followup.send(f"Approval error: {exc}", ephemeral=True)

    async def _handle_reject(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        rejected_by = f"{user.display_name} ({user.id})"

        try:
            with SessionLocal() as session:
                service = ApprovalService(session)
                job = service.reject_job(self.job_id, rejected_by=rejected_by)
                session.commit()

                embed = job_recommendation_embed(
                    job,
                    footer_note=f"Rejected by {rejected_by}",
                )
                disabled_view = JobActionView.disabled_view(self.job_id, job.job_url)

                if interaction.message:
                    await interaction.message.edit(embed=embed, view=disabled_view)

                await interaction.followup.send(
                    f"Rejected job `{self.job_id}`.",
                    ephemeral=True,
                )
        except (JobNotFoundError, ApprovalNotAllowedError) as exc:
            logger.warning("Reject failed for job %s: %s", self.job_id, exc)
            await interaction.followup.send(f"Cannot reject: {exc}", ephemeral=True)

    @staticmethod
    def disabled_view(job_id: int, job_url: str | None) -> discord.ui.View:
        """Return a view with Approve/Reject disabled (link button remains)."""
        view = discord.ui.View(timeout=None)
        view_url = job_url or "https://example.com/jobs/placeholder"
        view.add_item(
            discord.ui.Button(
                label="VIEW JOB",
                style=discord.ButtonStyle.link,
                url=view_url,
            )
        )
        view.add_item(
            discord.ui.Button(
                label="APPROVED / REJECTED",
                style=discord.ButtonStyle.secondary,
                disabled=True,
                custom_id=f"job_done:{job_id}",
            )
        )
        return view

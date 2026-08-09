"""Discord embed builders for job recommendations."""

from __future__ import annotations

import discord

from app.models.job import Job, JobStatus


def _salary_text(job: Job) -> str:
    if job.salary_min and job.salary_max:
        return f"${job.salary_min // 1000}k-${job.salary_max // 1000}k"
    if job.salary_min:
        return f"${job.salary_min // 1000}k+"
    if job.salary_max:
        return f"Up to ${job.salary_max // 1000}k"
    return "Not specified"


def _fit_text(job: Job) -> str:
    if job.fit_score is None:
        return "N/A"
    return f"{int(round(job.fit_score * 100))}% match"


def _status_color(status: JobStatus) -> discord.Color:
    mapping = {
        JobStatus.AWAITING_APPROVAL: discord.Color.gold(),
        JobStatus.APPROVED: discord.Color.green(),
        JobStatus.REJECTED: discord.Color.red(),
        JobStatus.APPLIED: discord.Color.blue(),
        JobStatus.NEEDS_USER: discord.Color.orange(),
        JobStatus.FAILED: discord.Color.dark_red(),
    }
    return mapping.get(status, discord.Color.blurple())


def job_recommendation_embed(job: Job, *, footer_note: str | None = None) -> discord.Embed:
    """Build the primary job recommendation embed."""
    status = job.status_enum
    title_prefix = ""
    if status == JobStatus.APPROVED:
        title_prefix = "✅ APPROVED — "
    elif status == JobStatus.REJECTED:
        title_prefix = "❌ REJECTED — "

    embed = discord.Embed(
        title=f"{title_prefix}{job.title}",
        description=job.company,
        color=_status_color(status),
        url=job.job_url,
    )
    embed.add_field(name="Location", value=job.remote_status or job.location or "—", inline=True)
    embed.add_field(name="Salary", value=_salary_text(job), inline=True)
    embed.add_field(name="Fit", value=_fit_text(job), inline=True)
    embed.add_field(name="Status", value=status.value, inline=True)
    embed.add_field(name="Job ID", value=str(job.id), inline=True)
    embed.add_field(name="Source", value=job.source, inline=True)

    if job.recommendation_reason:
        # Discord field value limit is 1024 chars
        reason = job.recommendation_reason[:1000]
        embed.add_field(name="Recommendation", value=reason, inline=False)

    note = footer_note or "Explicit Approve required before resume/application pipeline."
    embed.set_footer(text=f"{note} · id={job.id}")
    return embed


def system_status_embed(
    *,
    env: str,
    job_count: int,
    awaiting_count: int,
    approved_count: int,
) -> discord.Embed:
    """Build a basic system status embed."""
    embed = discord.Embed(
        title="AI Job Agent — Status",
        color=discord.Color.blurple(),
        description="Phase 1 control plane is online.",
    )
    embed.add_field(name="Environment", value=env, inline=True)
    embed.add_field(name="Total Jobs", value=str(job_count), inline=True)
    embed.add_field(name="Awaiting Approval", value=str(awaiting_count), inline=True)
    embed.add_field(name="Approved", value=str(approved_count), inline=True)
    return embed

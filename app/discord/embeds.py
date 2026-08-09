"""Discord embed builders for job recommendations and Scout evaluations."""

from __future__ import annotations

import discord

from app.models.job import Job, JobStatus
from app.schemas.evaluation import ScoutEvaluation


def _salary_text(job: Job) -> str:
    if job.salary_min and job.salary_max:
        return f"${job.salary_min // 1000}k-${job.salary_max // 1000}k"
    if job.salary_min:
        return f"${job.salary_min // 1000}k+"
    if job.salary_max:
        return f"Up to ${job.salary_max // 1000}k"
    return "Unknown"


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


def _truncate(text: str, limit: int = 1000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


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
    embed.add_field(
        name="Location",
        value=job.remote_status or job.location or "Unknown",
        inline=True,
    )
    embed.add_field(name="Salary", value=_salary_text(job), inline=True)
    embed.add_field(name="Fit", value=_fit_text(job), inline=True)
    embed.add_field(name="Status", value=status.value, inline=True)
    embed.add_field(name="Job ID", value=str(job.id), inline=True)
    embed.add_field(name="Source", value=job.source, inline=True)

    if job.recommendation_reason:
        reason = _truncate(job.recommendation_reason, 1000)
        embed.add_field(name="Recommendation", value=reason, inline=False)

    note = footer_note or "Explicit Approve required before resume/application pipeline."
    embed.set_footer(text=f"{note} · id={job.id}")
    return embed


def scout_evaluation_embed(
    job: Job,
    evaluation: ScoutEvaluation,
    *,
    footer_note: str | None = None,
    extraction_warnings: list[str] | None = None,
    extraction_confidence: str | None = None,
) -> discord.Embed:
    """Compact Scout evaluation embed for Discord review."""
    loc_bits = [x for x in (job.location, job.remote_status) if x]
    loc_line = " • ".join(loc_bits) if loc_bits else "Unknown location"
    desc = f"**{job.company}**\nAI JOB SCOUT\n{loc_line}"
    embed = discord.Embed(
        title=job.title,
        description=desc,
        color=_status_color(job.status_enum),
        url=job.job_url,
    )
    embed.add_field(
        name="QUALIFICATION",
        value=f"{evaluation.qualification_score}/100",
        inline=True,
    )
    embed.add_field(
        name="DESIRABILITY",
        value=f"{evaluation.desirability_score}/100",
        inline=True,
    )
    embed.add_field(
        name="CONFIDENCE",
        value=evaluation.confidence.value,
        inline=True,
    )
    embed.add_field(
        name="Recommendation",
        value=evaluation.recommendation.value,
        inline=True,
    )
    embed.add_field(name="Salary", value=_salary_text(job), inline=True)
    if extraction_confidence:
        embed.add_field(name="Extraction", value=extraction_confidence, inline=True)

    if evaluation.matching_skills:
        strong = "\n".join(f"✓ {s}" for s in evaluation.matching_skills[:6])
        embed.add_field(name="Strong evidence", value=_truncate(strong, 500), inline=False)
    if evaluation.partial_matches:
        partial = "\n".join(f"~ {s}" for s in evaluation.partial_matches[:4])
        embed.add_field(name="Partial evidence", value=_truncate(partial, 400), inline=False)
    if evaluation.missing_required_skills:
        missing = "\n".join(f"✗ {s}" for s in evaluation.missing_required_skills[:5])
        embed.add_field(name="Missing", value=_truncate(missing, 400), inline=False)

    strengths = [
        r for r in evaluation.desirability_reasoning if not r.lower().startswith("preference concern")
    ][:3]
    concerns = [
        r.replace("Preference concern: ", "", 1)
        for r in evaluation.desirability_reasoning
        if r.lower().startswith("preference concern")
    ][:3]
    if strengths:
        embed.add_field(
            name="Preference strengths",
            value=_truncate("\n".join(f"✓ {s}" for s in strengths), 500),
            inline=False,
        )
    if concerns:
        embed.add_field(
            name="Preference concerns",
            value=_truncate("\n".join(f"• {c}" for c in concerns), 400),
            inline=False,
        )
    if evaluation.uncertainties:
        unk = "\n".join(f"? {r}" for r in evaluation.uncertainties[:3])
        embed.add_field(name="Unknown", value=_truncate(unk, 400), inline=False)
    if extraction_warnings:
        warn = "\n".join(f"• {w}" for w in extraction_warnings[:3])
        embed.add_field(name="Extraction notes", value=_truncate(warn, 300), inline=False)

    if evaluation.qualification_reasoning:
        why = "\n".join(f"• {r}" for r in evaluation.qualification_reasoning[:2])
        embed.add_field(name="Why Scout scored qualification", value=_truncate(why, 400), inline=False)

    note = footer_note or (
        "Scout recommendation ≠ authorization. Explicit Approve required for this exact job."
    )
    embed.set_footer(text=f"{note} · job_id={job.id}")
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
        description="Control plane online. Scout evaluation foundation available.",
    )
    embed.add_field(name="Environment", value=env, inline=True)
    embed.add_field(name="Total Jobs", value=str(job_count), inline=True)
    embed.add_field(name="Awaiting Approval", value=str(awaiting_count), inline=True)
    embed.add_field(name="Approved", value=str(approved_count), inline=True)
    return embed

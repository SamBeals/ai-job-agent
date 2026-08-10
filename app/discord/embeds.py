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

    strong_lines: list[str] = []
    for item in evaluation.experience_matches[:2]:
        strong_lines.append(f"✓ {item}")
    for item in evaluation.matching_skills[:5]:
        strong_lines.append(f"✓ {item}")
    if strong_lines:
        embed.add_field(
            name="Strong Evidence",
            value=_truncate("\n".join(strong_lines[:6]), 500),
            inline=False,
        )

    if evaluation.partial_matches:
        partial = "\n".join(f"~ {s}" for s in evaluation.partial_matches[:4])
        embed.add_field(name="Partial Evidence", value=_truncate(partial, 400), inline=False)

    missing_lines: list[str] = []
    for s in evaluation.missing_required_skills[:4]:
        missing_lines.append(f"✗ {s} (required)")
    for s in evaluation.missing_preferred_skills[:3]:
        missing_lines.append(f"✗ {s} (preferred)")
    if missing_lines:
        embed.add_field(
            name="Missing / No Evidence",
            value=_truncate("\n".join(missing_lines), 400),
            inline=False,
        )

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
            name="Preference Strengths",
            value=_truncate("\n".join(f"✓ {s}" for s in strengths), 500),
            inline=False,
        )
    if concerns:
        embed.add_field(
            name="Preference Concerns",
            value=_truncate("\n".join(f"• {c}" for c in concerns), 400),
            inline=False,
        )
    if evaluation.uncertainties:
        unk = "\n".join(f"? {r}" for r in evaluation.uncertainties[:3])
        embed.add_field(name="Unknown", value=_truncate(unk, 400), inline=False)
    if extraction_warnings:
        warn = "\n".join(f"• {w}" for w in extraction_warnings[:3])
        embed.add_field(name="Extraction notes", value=_truncate(warn, 300), inline=False)

    # Prefer summary-style why over raw point dump
    why_bits: list[str] = []
    for r in evaluation.qualification_reasoning:
        if r.startswith("Required ") or r.startswith("Preferred ") or "(" in r[:20]:
            continue
        why_bits.append(r)
        if len(why_bits) >= 2:
            break
    if not why_bits and evaluation.qualification_reasoning:
        why_bits = evaluation.qualification_reasoning[:1]
    if why_bits:
        why = "\n".join(f"• {r}" for r in why_bits)
        embed.add_field(name="Why Scout recommends it", value=_truncate(why, 400), inline=False)

    eval_meta = (
        f"{evaluation.evaluator_provider}"
        f"{' / ' + evaluation.evaluator_model if evaluation.evaluator_model else ''}"
        f"{' · prompt ' + evaluation.prompt_version if evaluation.prompt_version else ''}"
    )
    note = footer_note or (
        "Scout recommendation ≠ authorization. Explicit Approve required for this exact job."
    )
    embed.set_footer(text=_truncate(f"{note} · {eval_meta} · job_id={job.id}", 2048))
    return embed


def scout_decision_embed(
    job: Job,
    evaluation: ScoutEvaluation,
    *,
    scout_channel_mention: str | None = None,
    resume_channel_mention: str | None = None,
) -> discord.Embed:
    """Compact control-plane decision card (APPROVE/REJECT live on the bot message)."""
    loc_bits = [x for x in (job.location, job.remote_status) if x]
    loc_line = " • ".join(loc_bits) if loc_bits else "Location unknown"
    embed = discord.Embed(
        title="Decision required",
        description=f"**{job.company}** — {job.title}\n{loc_line}",
        color=discord.Color.gold(),
        url=job.job_url,
    )
    embed.add_field(
        name="Qualification",
        value=f"{evaluation.qualification_score}/100",
        inline=True,
    )
    embed.add_field(
        name="Desirability",
        value=f"{evaluation.desirability_score}/100",
        inline=True,
    )
    embed.add_field(
        name="Recommendation",
        value=evaluation.recommendation.value,
        inline=True,
    )
    refs: list[str] = []
    if scout_channel_mention:
        refs.append(f"Detailed evaluation: {scout_channel_mention}")
    if resume_channel_mention:
        refs.append(f"After APPROVE: {resume_channel_mention}")
    if refs:
        embed.add_field(name="Follow the work", value="\n".join(refs), inline=False)
    embed.set_footer(
        text=(
            f"Scout recommendation ≠ authorization · Gate 1 APPROVE only · job_id={job.id}"
        )
    )
    return embed


def scout_evaluation_failed_embed(*, detail: str | None = None) -> discord.Embed:
    """User-facing embed when the configured LLM evaluator fails."""
    embed = discord.Embed(
        title="Scout evaluation unavailable",
        color=discord.Color.orange(),
        description=(
            "Scout could not complete the AI qualification analysis.\n\n"
            "The job was retrieved successfully, but the configured LLM evaluator failed.\n"
            "No recommendation was generated."
        ),
    )
    if detail:
        embed.add_field(name="Safe detail", value=_truncate(detail, 500), inline=False)
    embed.set_footer(text="Technical details were logged locally. Secrets are never shown.")
    return embed


def scout_detail_embed(job: Job, evaluation: ScoutEvaluation) -> discord.Embed:
    """Richer requirement-level detail for an already-evaluated job."""
    embed = discord.Embed(
        title=f"Scout detail — {job.title}",
        description=f"**{job.company}** · job_id={job.id}",
        color=discord.Color.blurple(),
        url=job.job_url,
    )
    embed.add_field(
        name="Scores",
        value=(
            f"Q {evaluation.qualification_score}/100 · "
            f"D {evaluation.desirability_score}/100 · "
            f"{evaluation.recommendation.value}"
        ),
        inline=False,
    )
    lines: list[str] = []
    for row in evaluation.requirement_matches[:12]:
        req = row.get("requirement") or {}
        name = req.get("name", "?")
        rtype = req.get("requirement_type", "")
        level = row.get("match_level", "")
        pts = row.get("contribution_points")
        pts_s = f" ({pts:+.1f})" if isinstance(pts, (int, float)) else ""
        lines.append(f"{rtype} {name}: {level}{pts_s}")
    if lines:
        embed.add_field(
            name="Requirement matches",
            value=_truncate("\n".join(lines), 1000),
            inline=False,
        )
    else:
        embed.add_field(
            name="Requirement matches",
            value="No structured requirement rows persisted for this evaluation.",
            inline=False,
        )
    embed.set_footer(
        text=(
            f"{evaluation.evaluator_provider}"
            f"{' / ' + evaluation.evaluator_model if evaluation.evaluator_model else ''}"
            f" · prompt {evaluation.prompt_version or 'n/a'} · not authorization"
        )
    )
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

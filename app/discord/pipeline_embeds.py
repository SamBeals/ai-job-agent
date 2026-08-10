"""Discord embeds for multi-agent control room."""

from __future__ import annotations

import discord

from app.models.approval import Approval
from app.models.job import Job
from app.models.pipeline import ApplicationPipeline
from app.models.resume_plan import ResumePlanRecord
from app.models.work_item import AgentWorkItem
from app.schemas.agents import AgentType, IMPLEMENTED_AGENTS, PipelineStatus
from app.schemas.evaluation import ScoutEvaluation
from app.schemas.resume_plan import ResumePlan


def _truncate(text: str, limit: int = 1000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def preparation_approved_embed(
    job: Job,
    pipeline: ApplicationPipeline,
    *,
    work_item_id: int | None,
    already: bool = False,
) -> discord.Embed:
    embed = discord.Embed(
        title="APPLICATION PREPARATION APPROVED",
        description=f"**{job.company}** — {job.title}",
        color=discord.Color.green(),
    )
    embed.add_field(
        name="Scout recommendation",
        value="Accepted" if not already else "Already authorized",
        inline=True,
    )
    embed.add_field(name="Pipeline", value=f"#{pipeline.id}", inline=True)
    embed.add_field(name="Next agent", value="📝 RESUME AGENT", inline=True)
    embed.add_field(
        name="Status",
        value="QUEUED" if work_item_id else pipeline.status,
        inline=True,
    )
    if work_item_id:
        embed.add_field(name="Work item", value=f"#{work_item_id}", inline=True)
    embed.set_footer(
        text="Preparation only — final application submission requires separate approval."
    )
    return embed


def pipeline_list_embed(rows: list[tuple[ApplicationPipeline, Job, dict[str, str]]]) -> discord.Embed:
    embed = discord.Embed(
        title="ACTIVE APPLICATION PIPELINES",
        color=discord.Color.blurple(),
    )
    if not rows:
        embed.description = "No active application pipelines."
        return embed
    lines: list[str] = []
    for pipeline, job, stages in rows:
        lines.append(
            f"**#{pipeline.id}** {job.company} — {job.title}\n"
            f"Scout: {stages.get('scout', '?')} · Prep: {stages.get('prep', '?')} · "
            f"Resume: {stages.get('resume', '?')} · Submit: {stages.get('submit', 'NO')}"
        )
    embed.description = _truncate("\n\n".join(lines), 3900)
    return embed


def pipeline_status_embed(
    *,
    pipeline: ApplicationPipeline,
    job: Job,
    approval: Approval | None,
    evaluation: ScoutEvaluation | None,
    resume_item: AgentWorkItem | None,
    resume_plan: ResumePlanRecord | None,
    can_submit: bool,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"APPLICATION PIPELINE #{pipeline.id}",
        description=f"**{job.company}** — {job.title}",
        color=discord.Color.blurple(),
        url=job.job_url,
    )
    if evaluation:
        embed.add_field(
            name="✓ Scout",
            value=(
                f"Qualification: {evaluation.qualification_score}\n"
                f"Desirability: {evaluation.desirability_score}"
            ),
            inline=False,
        )
    else:
        embed.add_field(name="○ Scout", value="No evaluation found", inline=False)

    if approval:
        when = approval.approved_at.strftime("%Y-%m-%d %H:%M UTC") if approval.approved_at else "?"
        embed.add_field(
            name="✓ Preparation approved",
            value=f"Approved by {approval.approved_by}\n{when}",
            inline=False,
        )
    else:
        embed.add_field(name="○ Preparation", value="Not approved", inline=False)

    if resume_plan:
        embed.add_field(
            name="✓ Resume Agent",
            value=f"Resume plan ready (#{resume_plan.id})",
            inline=False,
        )
    elif resume_item:
        embed.add_field(
            name="… Resume Agent",
            value=f"Status: {resume_item.status}",
            inline=False,
        )
    else:
        embed.add_field(name="○ Resume Agent", value="Not started", inline=False)

    embed.add_field(name="○ Resume Review", value="Not started", inline=False)
    embed.add_field(name="○ Applicant", value="Not started", inline=False)

    if can_submit:
        embed.add_field(name="🔓 Submission", value="Authorized", inline=False)
    else:
        embed.add_field(
            name="🔒 Submission",
            value="Requires final Gate 2 approval",
            inline=False,
        )

    embed.add_field(name="Pipeline status", value=pipeline.status, inline=True)
    embed.add_field(name="Job status", value=job.status, inline=True)
    return embed


def agents_status_embed(resume_counts: dict[str, int]) -> discord.Embed:
    embed = discord.Embed(title="AGENT STATUS", color=discord.Color.dark_teal())
    lines = [
        "🔎 **Scout** — READY",
        (
            f"📝 **Resume Agent** — "
            f"{resume_counts.get('PENDING', 0)} queued · "
            f"{resume_counts.get('RUNNING', 0)} running · "
            f"{resume_counts.get('COMPLETED', 0)} completed"
        ),
        "🔍 **Resume Review Agent** — NOT IMPLEMENTED",
        "🖥 **Applicant Agent** — NOT IMPLEMENTED",
        "📡 **Discovery Agent** — NOT IMPLEMENTED",
        "📬 **Tracker Agent** — NOT IMPLEMENTED",
    ]
    embed.description = "\n".join(lines)
    embed.set_footer(text="Agents cooperate via persisted state — not free-form chat.")
    return embed


def resume_plan_embed(job: Job, record: ResumePlanRecord) -> discord.Embed:
    plan = ResumePlan.model_validate(record.plan_json)
    embed = discord.Embed(
        title="RESUME PLAN",
        description=f"**{job.company}** — {job.title}",
        color=discord.Color.green(),
    )
    if plan.summary_strategy:
        embed.add_field(
            name="Positioning",
            value=_truncate(plan.summary_strategy, 500),
            inline=False,
        )
    if plan.priority_skills or plan.requirements_to_emphasize:
        items = plan.priority_skills or plan.requirements_to_emphasize
        lines = []
        for item in items[:6]:
            detail = f" — {item.source_detail}" if item.source_detail else ""
            lines.append(f"✓ {item.text}{detail}")
        embed.add_field(name="EMPHASIZE", value=_truncate("\n".join(lines), 600), inline=False)
    if plan.secondary_skills:
        lines = [f"~ {i.text}" + (f" — {i.source_detail}" if i.source_detail else "") for i in plan.secondary_skills[:4]]
        embed.add_field(name="SECONDARY", value=_truncate("\n".join(lines), 400), inline=False)
    if plan.skills_not_to_claim:
        lines = [f"✗ {s}" for s in plan.skills_not_to_claim[:8]]
        embed.add_field(name="DO NOT CLAIM", value=_truncate("\n".join(lines), 400), inline=False)
    if plan.gaps:
        lines = [f"• {g}" for g in plan.gaps[:5]]
        embed.add_field(name="GAPS", value=_truncate("\n".join(lines), 400), inline=False)
    embed.set_footer(
        text=f"plan #{record.id} · v{record.agent_version} · not a submission"
    )
    return embed

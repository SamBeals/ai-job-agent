"""Discord Scout embed rendering tests (no live Discord)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.discord.embeds import scout_evaluation_embed
from app.models.job import Job, JobStatus
from app.schemas.evaluation import Confidence, Recommendation, ScoutEvaluation


def test_discord_embed_handles_unknown_salary_location() -> None:
    job = Job(
        id=1,
        company="Opaque Systems",
        title="Backend Software Engineer",
        source="fixture",
        location=None,
        remote_status=None,
        salary_min=None,
        salary_max=None,
        job_url="https://example.com/jobs/x",
        status=JobStatus.AWAITING_APPROVAL.value,
        discovered_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    evaluation = ScoutEvaluation(
        job_id=1,
        qualification_score=80,
        desirability_score=70,
        recommendation=Recommendation.RECOMMEND,
        confidence=Confidence.LOW,
        matching_skills=["Java — professional experience"],
        uncertainties=[
            "Salary compatibility unknown because compensation was not listed.",
            "Work arrangement compatibility unknown.",
        ],
        qualification_reasoning=["Strong Java alignment."],
        desirability_reasoning=["Role preference unknown — not used in desirability."],
    )
    embed = scout_evaluation_embed(job, evaluation)
    field_map = {f.name: f.value for f in embed.fields}
    assert field_map["Salary"] == "Unknown"
    assert "Unknown location" in (embed.description or "")
    assert "QUALIFICATION" in field_map
    assert "80/100" in field_map["QUALIFICATION"]
    assert "Unknown" in field_map
    assert "Strong Evidence" in field_map
    assert "Why Scout recommends it" in field_map

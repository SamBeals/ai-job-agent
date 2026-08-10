"""Discord UI for manual Scout ingestion (fixture / URL / paste)."""

from __future__ import annotations

import logging

import discord

from app.agents.scout.ingestion import (
    DISCORD_DESCRIPTION_MAX,
    FIXTURE_CATALOG,
    IngestionError,
    JobIngestionService,
)
from app.agents.scout.llm.factory import LLMUnavailableError
from app.agents.scout.pipeline import ScoutEvaluationError, ScoutPipeline
from app.agents.scout.profile_loader import CandidateProfileError, load_candidate_profile
from app.config import Settings
from app.database.database import SessionLocal
from app.discord.embeds import scout_evaluation_embed, scout_evaluation_failed_embed
from app.discord.views import JobActionView
from app.models.job import JobStatus

logger = logging.getLogger(__name__)


class ScoutIngestView(discord.ui.View):
    """Top-level chooser: fixture / URL / paste."""

    def __init__(self, settings: Settings, *, timeout: float | None = 300) -> None:
        super().__init__(timeout=timeout)
        self.settings = settings

    @discord.ui.button(label="TEST FIXTURE", style=discord.ButtonStyle.primary)
    async def fixture_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "Choose a Scout test fixture:",
            view=FixtureSelectView(self.settings),
            ephemeral=True,
        )

    @discord.ui.button(label="JOB URL", style=discord.ButtonStyle.secondary)
    async def url_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(JobUrlModal(self.settings))

    @discord.ui.button(label="PASTE JOB", style=discord.ButtonStyle.secondary)
    async def paste_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(PasteJobModal(self.settings))


class FixtureSelectView(discord.ui.View):
    def __init__(self, settings: Settings, *, timeout: float | None = 300) -> None:
        super().__init__(timeout=timeout)
        self.settings = settings
        options = [
            discord.SelectOption(label=label[:100], value=key)
            for key, (_file, label) in FIXTURE_CATALOG.items()
        ]
        select = discord.ui.Select(placeholder="Select a fixture…", options=options)

        async def _on_select(interaction: discord.Interaction) -> None:
            key = select.values[0]
            await interaction.response.defer(ephemeral=True)
            await run_scout_ingestion(
                interaction,
                self.settings,
                source_label=f"fixture:{key}",
                ingest=lambda svc: svc.ingest_fixture(key),
                profile_override=(
                    "data/fixtures/profiles/test_remote_required.json"
                    if key == "c_onsite"
                    else None
                ),
            )

        select.callback = _on_select  # type: ignore[method-assign]
        self.add_item(select)


class JobUrlModal(discord.ui.Modal, title="Evaluate job URL"):
    url = discord.ui.TextInput(
        label="Job posting URL",
        placeholder="https://company.com/careers/job/123",
        style=discord.TextStyle.short,
        required=True,
        max_length=500,
    )

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        url = str(self.url.value).strip()
        await run_scout_ingestion(
            interaction,
            self.settings,
            source_label="url",
            ingest=lambda svc: svc.ingest_url(url),
        )


class PasteJobModal(discord.ui.Modal, title="Paste job description"):
    title_field = discord.ui.TextInput(
        label="Job Title (optional)",
        required=False,
        max_length=200,
        style=discord.TextStyle.short,
    )
    company_field = discord.ui.TextInput(
        label="Company (optional)",
        required=False,
        max_length=200,
        style=discord.TextStyle.short,
    )
    url_field = discord.ui.TextInput(
        label="Job URL (optional)",
        required=False,
        max_length=500,
        style=discord.TextStyle.short,
    )
    description_field = discord.ui.TextInput(
        label="Job Description",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=DISCORD_DESCRIPTION_MAX,
        placeholder="Paste the job posting text (max 4000 chars). For longer posts use the CLI.",
    )

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        text = str(self.description_field.value or "")
        if not text.strip():
            await interaction.followup.send("Job description is empty.", ephemeral=True)
            return

        # Discord caps at 4000 — if user hit the cap, treat as potentially partial
        partial = len(text) >= DISCORD_DESCRIPTION_MAX
        if partial:
            await interaction.followup.send(
                "⚠️ Description hit Discord's 4000-character modal limit. "
                "Scout will mark this as **partial content** and lower extraction confidence. "
                "For full postings use:\n"
                "`python -m app.agents.scout.evaluate --file ./job.txt`",
                ephemeral=True,
            )

        title = str(self.title_field.value).strip() or None
        company = str(self.company_field.value).strip() or None
        source_url = str(self.url_field.value).strip() or None

        await run_scout_ingestion(
            interaction,
            self.settings,
            source_label="text",
            ingest=lambda svc: svc.ingest_text(
                text,
                title=title,
                company=company,
                source_url=source_url,
                partial_content=partial,
            ),
        )


async def run_scout_ingestion(
    interaction: discord.Interaction,
    settings: Settings,
    *,
    source_label: str,
    ingest,
    profile_override: str | None = None,
) -> None:
    """Shared Discord follow-up path after deferral."""
    try:
        ingestion = JobIngestionService(settings)
        extraction = ingest(ingestion)
        profile_path = profile_override or settings.candidate_profile_path
        profile = load_candidate_profile(profile_path)

        with SessionLocal() as session:
            pipeline = ScoutPipeline(settings=settings, session=session)
            result = pipeline.evaluate(
                extraction.normalized_job,
                profile,
                persist=True,
                create_job_record=True,
                source_content_partial=extraction.partial_content,
                extraction_confidence=extraction.extraction_confidence.value,
            )
            session.commit()
            if result.job is None:
                await interaction.followup.send(
                    "Evaluation completed but no job was persisted.",
                    ephemeral=True,
                )
                return
            session.refresh(result.job)
            embed = scout_evaluation_embed(
                result.job,
                result.evaluation,
                extraction_warnings=extraction.warnings,
                extraction_confidence=extraction.extraction_confidence.value,
            )
            view = (
                JobActionView(result.job.id, result.job.job_url, timeout=None)
                if result.job.status_enum == JobStatus.AWAITING_APPROVAL
                else None
            )
            send_kwargs: dict = {
                "content": (
                    f"**Scout evaluation** (`{source_label}`)\n"
                    f"Extraction: `{extraction.extraction_method.value}` "
                    f"/ confidence `{extraction.extraction_confidence.value}`\n"
                    f"Evaluator: `{result.evaluation.evaluator_provider}` "
                    f"/ `{result.evaluation.evaluator_model or 'n/a'}`\n"
                    f"Present to user: `{result.should_present}`\n"
                    "Scout recommendation is **not** authorization. "
                    "Only **APPROVE** authorizes this exact job."
                ),
                "embed": embed,
                "ephemeral": True,
            }
            if view is not None:
                send_kwargs["view"] = view
            await interaction.followup.send(**send_kwargs)
    except IngestionError as exc:
        logger.warning("scout ingestion failed: %s", exc)
        await interaction.followup.send(str(exc), ephemeral=True)
    except CandidateProfileError as exc:
        await interaction.followup.send(f"Candidate profile error: {exc}", ephemeral=True)
    except ScoutEvaluationError as exc:
        logger.exception("scout evaluation failed for Discord ingestion")
        await interaction.followup.send(
            embed=scout_evaluation_failed_embed(
                detail="Configured evaluator failed. No recommendation was generated."
            ),
            ephemeral=True,
        )
    except LLMUnavailableError as exc:
        logger.exception("scout LLM provider unavailable")
        await interaction.followup.send(
            embed=scout_evaluation_failed_embed(
                detail=str(exc),
            ),
            ephemeral=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Unexpected scout ingestion failure")
        await interaction.followup.send(
            "Scout hit an unexpected error. Details were logged locally.",
            ephemeral=True,
        )

"""Unified Scout evaluate CLI — fixture / URL / file → same pipeline.

Examples:
  python -m app.agents.scout.evaluate --fixture a_strong_backend
  python -m app.agents.scout.evaluate --url "https://example.com/jobs/123"
  python -m app.agents.scout.evaluate --file ./job_description.txt
  python -m app.agents.scout.evaluate --file ./job.txt --provider mock
  python -m app.agents.scout.evaluate --file ./job.txt --provider openai
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from app.agents.scout.ingestion import (
    FIXTURE_CATALOG,
    IngestionError,
    JobIngestionService,
)
from app.agents.scout.llm.factory import LLMUnavailableError, get_llm_client
from app.agents.scout.pipeline import ScoutEvaluationError, ScoutPipeline
from app.agents.scout.profile_loader import CandidateProfileError, load_candidate_profile
from app.config import get_settings
from app.database.database import SessionLocal, init_db
from app.schemas.job_posting import NormalizedJob


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Ingest + evaluate a job with Scout (never authorizes).")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--fixture", help="Fixture key: " + ", ".join(FIXTURE_CATALOG))
    src.add_argument("--url", help="Public job posting URL")
    src.add_argument("--file", help="Path to job description text or NormalizedJob JSON")
    p.add_argument("--profile", default=None, help="Candidate profile path")
    p.add_argument("--title", default=None, help="Optional title override for --file text")
    p.add_argument("--company", default=None, help="Optional company override for --file text")
    p.add_argument(
        "--provider",
        default=None,
        choices=["mock", "openai"],
        help="Override LLM_PROVIDER for this run only (does not mutate .env)",
    )
    p.add_argument("--persist", action="store_true", help="Persist Job + ScoutEvaluation")
    p.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    args = _build_parser().parse_args(argv)
    base = get_settings()
    settings = (
        base.model_copy(update={"llm_provider": args.provider})
        if args.provider
        else base
    )
    profile_path = args.profile or settings.candidate_profile_path
    ingestion = JobIngestionService(settings)

    try:
        if args.fixture:
            extraction = ingestion.ingest_fixture(args.fixture)
            if args.fixture.strip().lower() == "c_onsite":
                profile_path = "data/fixtures/profiles/test_remote_required.json"
        elif args.url:
            extraction = ingestion.ingest_url(args.url)
        else:
            path = Path(args.file)
            raw = path.read_text(encoding="utf-8")
            # JSON NormalizedJob shortcut
            if path.suffix.lower() == ".json":
                job = NormalizedJob.model_validate(json.loads(raw))
                from app.agents.scout.ingestion.models import (
                    ExtractionConfidence,
                    ExtractionMethod,
                    ExtractionResult,
                    InputSource,
                )

                extraction = ExtractionResult(
                    normalized_job=job,
                    input_source=InputSource.TEXT,
                    extraction_method=ExtractionMethod.MANUAL_FIELDS,
                    extraction_confidence=ExtractionConfidence.HIGH,
                )
            else:
                extraction = ingestion.ingest_text(
                    raw,
                    title=args.title,
                    company=args.company,
                )
        candidate = load_candidate_profile(profile_path)
        llm_client = get_llm_client(settings)
    except (IngestionError, CandidateProfileError, OSError, ValueError, LLMUnavailableError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    session = None
    if args.persist:
        init_db()
        session = SessionLocal()

    try:
        pipeline = ScoutPipeline(
            settings=settings,
            session=session,
            llm_client=llm_client,
        )
        result = pipeline.evaluate(
            extraction.normalized_job,
            candidate,
            persist=args.persist,
            create_job_record=args.persist,
            source_content_partial=extraction.partial_content,
            extraction_confidence=extraction.extraction_confidence.value,
        )
        if args.persist and session is not None:
            session.commit()
    except ScoutEvaluationError as exc:
        if session is not None:
            session.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if session is not None:
            session.close()

    evaluation = result.evaluation
    job = extraction.normalized_job

    if args.json:
        payload = {
            "extraction": extraction.model_dump(mode="json"),
            "evaluation": evaluation.model_dump(mode="json"),
            "should_present": result.should_present,
            "persisted_job_id": result.job.id if result.job else None,
        }
        print(json.dumps(payload, indent=2))
        return 0

    print("=" * 60)
    print(f"Source: {extraction.input_source.value} / {extraction.extraction_method.value}")
    print(f"Extraction confidence: {extraction.extraction_confidence.value}")
    print(
        f"Evaluator: {evaluation.evaluator_provider}"
        f" / {evaluation.evaluator_model or 'n/a'}"
        f" / prompt {evaluation.prompt_version or 'n/a'}"
    )
    if extraction.warnings:
        print("Extraction warnings:")
        for w in extraction.warnings:
            print(f"  • {w}")
    print(f"Job: {job.title} @ {job.company}")
    print(f"Location: {job.location or 'Unknown'} | Arrangement: {job.remote_status or 'Unknown'}")
    print(f"Salary: {job.salary_min}-{job.salary_max} {job.salary_currency or ''}".strip())
    print(f"Qualification: {evaluation.qualification_score}/100")
    print(f"Desirability:  {evaluation.desirability_score}/100")
    print(f"Confidence:    {evaluation.confidence.value}")
    print(f"Recommendation:{evaluation.recommendation.value}")
    print(f"Present to user: {result.should_present}")
    if result.job is not None:
        print(f"Persisted job_id: {result.job.id} status={result.job.status}")
        print("NOTE: Recommendation is NOT authorization.")
    print("-" * 60)
    print("Qualification reasoning:")
    for line in evaluation.qualification_reasoning:
        print(f"  • {line}")
    print("Desirability reasoning:")
    for line in evaluation.desirability_reasoning:
        print(f"  • {line}")
    if evaluation.uncertainties:
        print("Uncertainties:")
        for line in evaluation.uncertainties:
            print(f"  • {line}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

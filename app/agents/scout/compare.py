"""Compare Scout providers on the same job (calibration helper).

Example:
  python -m app.agents.scout.compare --file ./job.txt --providers mock,openai
  python -m app.agents.scout.compare --fixture g_calibration_se --providers mock,openai
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.agents.scout.ingestion import FIXTURE_CATALOG, IngestionError, JobIngestionService
from app.agents.scout.llm.factory import LLMUnavailableError, get_llm_client
from app.agents.scout.pipeline import ScoutEvaluationError, ScoutPipeline
from app.agents.scout.profile_loader import CandidateProfileError, load_candidate_profile
from app.config import get_settings
from app.schemas.evaluation import ScoutEvaluation
from app.schemas.job_posting import NormalizedJob


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compare Scout evaluators (never authorizes).")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--fixture", help="Fixture key: " + ", ".join(FIXTURE_CATALOG))
    src.add_argument("--file", help="Job text or NormalizedJob JSON")
    p.add_argument("--profile", default=None)
    p.add_argument(
        "--providers",
        default="mock,openai",
        help="Comma-separated providers (default: mock,openai)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    base = get_settings()
    profile_path = args.profile or base.candidate_profile_path
    providers = [p.strip().lower() for p in args.providers.split(",") if p.strip()]
    if not providers:
        print("ERROR: no providers specified", file=sys.stderr)
        return 2

    ingestion = JobIngestionService(base)
    try:
        if args.fixture:
            extraction = ingestion.ingest_fixture(args.fixture)
            if args.fixture.strip().lower() == "c_onsite":
                profile_path = "data/fixtures/profiles/test_remote_required.json"
        else:
            path = Path(args.file)
            raw = path.read_text(encoding="utf-8")
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
                extraction = ingestion.ingest_text(raw)
        candidate = load_candidate_profile(profile_path)
    except (IngestionError, CandidateProfileError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    results: dict[str, ScoutEvaluation] = {}
    errors: dict[str, str] = {}
    for provider in providers:
        settings = base.model_copy(update={"llm_provider": provider})
        try:
            client = get_llm_client(settings)
            pipeline = ScoutPipeline(settings=settings, llm_client=client)
            result = pipeline.evaluate(
                extraction.normalized_job,
                candidate,
                source_content_partial=extraction.partial_content,
                extraction_confidence=extraction.extraction_confidence.value,
            )
            results[provider] = result.evaluation
        except (LLMUnavailableError, ScoutEvaluationError) as exc:
            errors[provider] = str(exc)

    # Summary table
    cols = providers
    print(f"{'':18}" + "".join(f"{c.upper():>12}" for c in cols))
    rows = [
        ("Qualification", lambda e: str(e.qualification_score)),
        ("Desirability", lambda e: str(e.desirability_score)),
        ("Confidence", lambda e: e.confidence.value),
        ("Recommendation", lambda e: e.recommendation.value),
    ]
    for label, fn in rows:
        line = f"{label:18}"
        for c in cols:
            if c in results:
                line += f"{fn(results[c]):>12}"
            else:
                line += f"{'ERROR':>12}"
        print(line)

    if errors:
        print("\nProvider errors (no silent fallback):")
        for p, msg in errors.items():
            print(f"  {p}: {msg}")

    # Requirement-level diffs when both present
    if len(results) >= 2:
        print("\nRequirement-level highlights:")
        for provider, evaluation in results.items():
            print(f"\n[{provider}] qualification_reasoning (top 8):")
            for line in evaluation.qualification_reasoning[:8]:
                print(f"  • {line}")
            if evaluation.matching_skills:
                print("  Strong:", ", ".join(evaluation.matching_skills[:6]))
            if evaluation.partial_matches:
                print("  Partial:", ", ".join(evaluation.partial_matches[:4]))
            if evaluation.missing_required_skills:
                print("  Missing required:", ", ".join(evaluation.missing_required_skills[:5]))
            if evaluation.missing_preferred_skills:
                print("  Missing preferred:", ", ".join(evaluation.missing_preferred_skills[:5]))

    print("\nNOTE: Comparison never authorizes applications.")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI: evaluate a manually supplied job JSON against the candidate profile.

Usage:
  python -m app.agents.scout.evaluate_job path/to/job.json
  python -m app.agents.scout.evaluate_job path/to/job.json --profile data/candidate_profile.json
  python -m app.agents.scout.evaluate_job path/to/job.json --persist
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from app.agents.scout.pipeline import ScoutEvaluationError, ScoutPipeline
from app.agents.scout.profile_loader import CandidateProfileError, load_candidate_profile
from app.config import get_settings
from app.database.database import SessionLocal, init_db
from app.schemas.job_posting import NormalizedJob


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scout test harness — evaluate a job JSON without authorizing it."
    )
    parser.add_argument("job_path", type=str, help="Path to NormalizedJob JSON fixture")
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Path to candidate_profile.json (default from settings)",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Persist Job + ScoutEvaluation to the database (still requires Discord approve)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON only",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    profile_path = args.profile or settings.candidate_profile_path

    try:
        candidate = load_candidate_profile(profile_path)
        job = NormalizedJob.model_validate(
            json.loads(Path(args.job_path).read_text(encoding="utf-8"))
        )
    except (CandidateProfileError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    session = None
    if args.persist:
        init_db()
        session = SessionLocal()

    try:
        pipeline = ScoutPipeline(settings=settings, session=session)
        result = pipeline.evaluate(
            job,
            candidate,
            persist=args.persist,
            create_job_record=args.persist,
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
    if args.json:
        print(json.dumps(evaluation.model_dump(mode="json"), indent=2))
    else:
        print("=" * 60)
        print(f"Job: {job.title} @ {job.company}")
        print(f"Qualification: {evaluation.qualification_score}/100")
        print(f"Desirability:  {evaluation.desirability_score}/100")
        print(f"Confidence:    {evaluation.confidence.value}")
        print(f"Recommendation:{evaluation.recommendation.value}")
        print(f"Present to user: {result.should_present}")
        if result.job is not None:
            print(f"Persisted job_id: {result.job.id} status={result.job.status}")
            print(
                "NOTE: Recommendation is NOT authorization. "
                "Use Discord APPROVE for this exact job."
            )
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
        if evaluation.matching_skills:
            print("Matching skills:")
            for line in evaluation.matching_skills:
                print(f"  ✓ {line}")
        if evaluation.partial_matches:
            print("Partial matches:")
            for line in evaluation.partial_matches:
                print(f"  ~ {line}")
        if evaluation.missing_required_skills:
            print("Missing required:")
            for line in evaluation.missing_required_skills:
                print(f"  ✗ {line}")
        print("=" * 60)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

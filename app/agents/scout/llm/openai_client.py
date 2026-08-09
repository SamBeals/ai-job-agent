"""OpenAI Scout qualification evaluator — structured SemanticJobEvaluation only."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from pydantic import ValidationError

from app.agents.scout.assembler import assemble_scout_evaluation
from app.agents.scout.evidence_payload import (
    assert_payload_has_no_sensitive_fields,
    build_candidate_evidence_payload,
)
from app.agents.scout.llm.base import DeterministicContext
from app.agents.scout.prompts.qualification import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.schemas.candidate import CandidateProfile
from app.schemas.evaluation import HardFilterResult, ScoutEvaluation
from app.schemas.evidence import SkillMatchReport
from app.schemas.job_posting import NormalizedJob
from app.schemas.qualification import SemanticJobEvaluation, TokenUsage

logger = logging.getLogger(__name__)


class EvaluatorOutputError(Exception):
    """Raised when provider output cannot be validated. Never fabricate scores."""


class OpenAIScoutClient:
    """Evidence-grounded OpenAI qualifier.

    Returns ScoutEvaluation assembled in code:
    - qualification from structured SemanticJobEvaluation + deterministic scoring
    - desirability from deterministic preference scorer
    - hard filters remain authoritative

    Never silently falls back to mock on failure.
    """

    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        evaluator_version: str = "2a.6",
        prompt_version: str = PROMPT_VERSION,
        temperature: float = 0.1,
    ) -> None:
        if not api_key:
            raise EvaluatorOutputError("OPENAI_API_KEY is required for LLM_PROVIDER=openai")
        self.api_key = api_key
        self.model = model
        self.evaluator_version = evaluator_version
        self.prompt_version = prompt_version
        self.temperature = temperature

    def evaluate_job(
        self,
        candidate: CandidateProfile,
        job: NormalizedJob,
        deterministic_context: DeterministicContext,
    ) -> ScoutEvaluation:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise EvaluatorOutputError(
                "openai package is not installed; pip install openai "
                "or set LLM_PROVIDER=mock"
            ) from exc

        skill_report = SkillMatchReport.model_validate(
            deterministic_context.get("skill_report") or {}
        )
        hard_filter = HardFilterResult.model_validate(
            deterministic_context.get("hard_filter") or {}
        )
        partial = bool(deterministic_context.get("source_content_partial"))
        extraction_confidence = deterministic_context.get("extraction_confidence")
        version = str(
            deterministic_context.get("evaluator_version") or self.evaluator_version
        )

        evidence = build_candidate_evidence_payload(candidate)
        assert_payload_has_no_sensitive_fields(evidence)

        job_payload = {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "remote_status": job.remote_status,
            "employment_type": job.employment_type,
            "description": job.description,
            "responsibilities": job.responsibilities,
            "required_skills": job.required_skills,
            "preferred_skills": job.preferred_skills,
            "required_years_experience": job.required_years_experience,
            "education_requirements": job.education_requirements,
            "seniority": job.seniority,
            "salary_min": job.salary_min,
            "salary_max": job.salary_max,
            # Preferences intentionally omitted — desirability is deterministic
        }

        user_prompt = build_user_prompt(
            job_payload=job_payload,
            candidate_evidence=evidence,
            skill_report=skill_report.model_dump(mode="json"),
            source_content_partial=partial,
            schema=SemanticJobEvaluation.model_json_schema(),
        )

        client = OpenAI(api_key=self.api_key)
        started = time.perf_counter()
        try:
            # Prefer structured parse when available; fall back to json_object.
            semantic, usage = self._call_model(client, user_prompt)
        except EvaluatorOutputError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("OpenAI scout evaluation failed")
            raise EvaluatorOutputError(f"LLM unavailable: {exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if usage and usage.latency_ms is None:
            usage = usage.model_copy(update={"latency_ms": latency_ms, "model": self.model})

        logger.info(
            "openai_qualification_success model=%s tokens=%s latency_ms=%s",
            self.model,
            usage.total_tokens if usage else None,
            latency_ms,
        )

        return assemble_scout_evaluation(
            candidate=candidate,
            job=job,
            semantic=semantic,
            hard_filter=hard_filter,
            provider=self.provider_name,
            evaluator_version=version,
            prompt_version=self.prompt_version,
            model=self.model,
            token_usage=usage,
            source_content_partial=partial,
            extraction_confidence=str(extraction_confidence) if extraction_confidence else None,
        )

    def _call_model(self, client: Any, user_prompt: str) -> tuple[SemanticJobEvaluation, TokenUsage | None]:
        # Try responses/parse-style structured output if present on SDK
        try:
            if hasattr(client, "beta") and hasattr(client.beta, "chat"):
                parse_fn = getattr(client.beta.chat.completions, "parse", None)
                if callable(parse_fn):
                    response = parse_fn(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        response_format=SemanticJobEvaluation,
                        temperature=self.temperature,
                    )
                    parsed = response.choices[0].message.parsed
                    if parsed is None:
                        raise EvaluatorOutputError("OpenAI returned empty structured parse result")
                    usage = _usage_from_response(response, self.model)
                    return parsed, usage
        except EvaluatorOutputError:
            raise
        except Exception:
            logger.info("structured parse unavailable; falling back to json_object mode")

        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=self.temperature,
        )
        content = response.choices[0].message.content or ""
        try:
            data = json.loads(content)
            semantic = SemanticJobEvaluation.model_validate(data)
        except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.exception("OpenAI qualification validation failed")
            raise EvaluatorOutputError(f"Invalid evaluator output: {exc}") from exc
        usage = _usage_from_response(response, self.model)
        return semantic, usage


def _usage_from_response(response: Any, model: str) -> TokenUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage(model=model)
    return TokenUsage(
        model=model,
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None),
    )

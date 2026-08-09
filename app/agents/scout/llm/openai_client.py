"""Optional OpenAI Scout evaluator — structured output only; fails safely."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from app.agents.scout.llm.base import DeterministicContext
from app.agents.scout.llm.mock import MockLLMClient
from app.schemas.candidate import CandidateProfile
from app.schemas.evaluation import ScoutEvaluation
from app.schemas.job_posting import NormalizedJob

logger = logging.getLogger(__name__)


class EvaluatorOutputError(Exception):
    """Raised when provider output cannot be validated into ScoutEvaluation."""


class OpenAIScoutClient:
    """Thin OpenAI wrapper. Requires openai package only when actually used.

    If the API call or validation fails, raises EvaluatorOutputError —
    callers must not fabricate scores.
    """

    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        evaluator_version: str = "2a.1",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.evaluator_version = evaluator_version
        # Fallback scoring helper for assembling context; not used as silent substitute
        self._mock = MockLLMClient(evaluator_version=evaluator_version)

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
                "openai package is not installed; use LLM_PROVIDER=mock or pip install openai"
            ) from exc

        # Start from deterministic baseline then ask model to refine reasoning.
        # If the API fails, we do NOT silently return the mock as success.
        baseline = self._mock.evaluate_job(candidate, job, deterministic_context)

        client = OpenAI(api_key=self.api_key)
        schema_hint = ScoutEvaluation.model_json_schema()
        prompt = {
            "instructions": (
                "Refine the scout evaluation JSON. Preserve qualification vs desirability "
                "as separate scores. Do not invent skills, employers, or years of experience. "
                "Unknown preferences must not penalize desirability. "
                "Return ONLY valid JSON matching the schema."
            ),
            "baseline": baseline.model_dump(mode="json"),
            "job": job.model_dump(mode="json"),
            "deterministic_context": _jsonable(deterministic_context),
            "schema": schema_hint,
        }

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a careful job-evaluation assistant. Never authorize applications.",
                    },
                    {"role": "user", "content": json.dumps(prompt, default=str)},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            content = response.choices[0].message.content or ""
            data = json.loads(content)
            data["evaluator_version"] = self.evaluator_version
            data["evaluator_provider"] = self.provider_name
            return ScoutEvaluation.model_validate(data)
        except (ValidationError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            logger.exception("OpenAI scout evaluation validation failed")
            raise EvaluatorOutputError(f"Invalid evaluator output: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — fail safely for any provider error
            logger.exception("OpenAI scout evaluation failed")
            raise EvaluatorOutputError(f"LLM unavailable: {exc}") from exc


def _jsonable(context: DeterministicContext) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in context.items():
        if hasattr(value, "model_dump"):
            out[key] = value.model_dump(mode="json")
        else:
            out[key] = value
    return out

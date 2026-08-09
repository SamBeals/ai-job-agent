"""LLM client factory — selects provider from settings."""

from __future__ import annotations

import logging

from app.agents.scout.llm.base import LLMClient
from app.agents.scout.llm.mock import MockLLMClient
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class LLMUnavailableError(Exception):
    """Raised when a configured LLM provider cannot be used."""


def get_llm_client(settings: Settings | None = None) -> LLMClient:
    """Return an LLM client.

    Missing OpenAI configuration fails clearly — never silently falls back to mock.
    """
    settings = settings or get_settings()
    provider = (settings.llm_provider or "mock").strip().lower()
    version = settings.scout_evaluator_version
    prompt_version = settings.scout_prompt_version

    if provider in {"", "mock", "deterministic"}:
        return MockLLMClient(
            evaluator_version=version,
            prompt_version=prompt_version,
        )

    if provider in {"openai", "openai_chat"}:
        from app.agents.scout.llm.openai_client import OpenAIScoutClient

        if not settings.openai_api_key:
            raise LLMUnavailableError(
                "LLM_PROVIDER=openai but OPENAI_API_KEY is not set. "
                "Set OPENAI_API_KEY or use LLM_PROVIDER=mock. "
                "Refusing silent mock fallback."
            )
        if (settings.llm_failure_fallback or "none").strip().lower() not in {
            "",
            "none",
        }:
            logger.warning(
                "LLM_FAILURE_FALLBACK=%s is configured but only 'none' is "
                "supported in this version; failures will still surface as errors.",
                settings.llm_failure_fallback,
            )
        return OpenAIScoutClient(
            api_key=settings.openai_api_key,
            model=settings.llm_model or "gpt-4o-mini",
            evaluator_version=version,
            prompt_version=prompt_version,
            temperature=settings.llm_temperature,
        )

    raise LLMUnavailableError(f"Unsupported LLM_PROVIDER: {provider}")

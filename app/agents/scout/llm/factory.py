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
    """Return an LLM client. Defaults to mock (no paid API required)."""
    settings = settings or get_settings()
    provider = (settings.llm_provider or "mock").strip().lower()
    version = settings.scout_evaluator_version

    if provider in {"", "mock", "deterministic", "none"}:
        return MockLLMClient(evaluator_version=version)

    if provider in {"openai", "openai_chat"}:
        # Optional provider — only constructed when explicitly configured.
        from app.agents.scout.llm.openai_client import OpenAIScoutClient

        if not settings.openai_api_key:
            raise LLMUnavailableError(
                "LLM_PROVIDER=openai but OPENAI_API_KEY is not set"
            )
        return OpenAIScoutClient(
            api_key=settings.openai_api_key,
            model=settings.llm_model or "gpt-4o-mini",
            evaluator_version=version,
        )

    raise LLMUnavailableError(f"Unsupported LLM_PROVIDER: {provider}")

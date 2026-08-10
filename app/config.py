"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for AI Job Agent."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Discord
    discord_bot_token: str = ""
    discord_guild_id: str = ""
    discord_channel_id: str = ""
    # Agent activity webhook (secret) — per-AgentType username/avatar overrides
    discord_agent_webhook_url: str = ""
    discord_webhook_timeout_seconds: float = 5.0
    scout_avatar_url: str = ""
    resume_avatar_url: str = ""
    resume_review_avatar_url: str = ""
    applicant_avatar_url: str = ""
    discovery_avatar_url: str = ""
    tracker_avatar_url: str = ""

    # Application
    app_env: str = "development"
    database_url: str = "sqlite:///./data/ai_job_agent.db"
    enable_test_commands: bool = True
    candidate_profile_path: str = "./data/candidate_profile.json"

    # FastAPI
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # Scout / LLM
    llm_provider: str = "mock"
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.1
    openai_api_key: str = ""
    llm_failure_fallback: str = "none"  # none | never silently use mock
    scout_prompt_version: str = "qualification-v1"
    scout_evaluator_version: str = "2a.6"
    scout_min_qualification_score: int = 55
    scout_min_desirability_score: int = 50
    scout_strong_qualification_score: int = 80
    scout_strong_desirability_score: int = 75
    scout_maybe_qualification_score: int = 40
    scout_maybe_desirability_score: int = 40

    # Manual job ingestion (Phase 2A.5)
    ingestion_http_timeout_seconds: float = 15.0
    ingestion_max_response_bytes: int = 2_000_000
    ingestion_max_redirects: int = 5
    ingestion_user_agent: str = (
        "AI-Job-Agent-Scout/2A.5 (+local; manual job evaluation; not a crawler)"
    )
    ingestion_extractor_version: str = "2a.5.1"

    # Multi-agent pipeline (Phase 2A.7 / Phase 3 foundation)
    agent_max_attempts: int = 3
    agent_worker_poll_seconds: float = 2.0
    resume_agent_version: str = "3.0.0"

    # Discovery Agent (Phase 3.2)
    # auto | fake | greenhouse | remotive | comma-separated
    discovery_provider: str = "auto"
    discovery_max_raw_results: int = 100
    discovery_max_surfaced_results: int = 10
    discovery_result_max_age_days: int = 30
    discovery_http_timeout_seconds: float = 15.0
    discovery_enable_remotive: bool = True
    # Comma-separated Greenhouse board tokens (public Job Board API, no key)
    discovery_greenhouse_boards: str = (
        "stripe,datadog,cloudflare,gitlab,hashicorp,twilio,notion,airbnb,discord"
    )
    # Optional token:Company Name;token2:Other
    discovery_greenhouse_company_names: str = (
        "stripe:Stripe;datadog:Datadog;cloudflare:Cloudflare;gitlab:GitLab;"
        "hashicorp:HashiCorp;twilio:Twilio;notion:Notion;airbnb:Airbnb;discord:Discord"
    )
    discovery_api_key: str = ""  # reserved for future paid APIs; unused by MVP providers

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in {"development", "dev", "local", "test"}


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()

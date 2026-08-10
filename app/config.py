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

    # Discovery Agent (Phase 3.2 / 3.3)
    # auto | fake | greenhouse,lever,ashby,remotive,muse,adzuna
    discovery_provider: str = "auto"
    discovery_max_raw_results: int = 100
    discovery_max_surfaced_results: int = 10
    # Ceiling companion: only surface results at/above this score (max is a ceiling).
    # Rationale: Chandler/Phoenix backend/hybrid ~70–95, US-remote backend ~55–70,
    # specialized/unknown-location roles typically ≤25. 45 blocks weak padding.
    discovery_min_surface_score: int = 45
    discovery_result_max_age_days: int = 30
    discovery_http_timeout_seconds: float = 15.0
    # Employer registry (Greenhouse / Lever / Ashby tenants). Env lists still merge in.
    discovery_boards_path: str = "config/discovery_boards.json"
    # Per-provider enable flags
    discovery_greenhouse_enabled: bool = True
    discovery_lever_enabled: bool = True
    discovery_ashby_enabled: bool = True
    discovery_remotive_enabled: bool = True
    discovery_enable_remotive: bool = True  # legacy alias
    discovery_muse_enabled: bool = True
    discovery_adzuna_enabled: bool = False  # requires keys + attribution readiness
    # Comma-separated ATS tenants (merged with discovery_boards.json)
    discovery_greenhouse_boards: str = (
        "stripe,datadog,cloudflare,gitlab,hashicorp,twilio,airbnb,discord"
    )
    discovery_greenhouse_company_names: str = (
        "stripe:Stripe;datadog:Datadog;cloudflare:Cloudflare;gitlab:GitLab;"
        "hashicorp:HashiCorp;twilio:Twilio;airbnb:Airbnb;discord:Discord"
    )
    discovery_lever_sites: str = ""
    discovery_lever_company_names: str = ""
    discovery_ashby_boards: str = ""
    discovery_ashby_company_names: str = ""
    # Adzuna (optional Type B)
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    discovery_adzuna_app_id: str = ""
    discovery_adzuna_app_key: str = ""
    discovery_api_key: str = ""  # reserved

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in {"development", "dev", "local", "test"}


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()

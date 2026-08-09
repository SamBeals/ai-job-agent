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
    llm_model: str = ""
    openai_api_key: str = ""
    scout_evaluator_version: str = "2a.1"
    scout_min_qualification_score: int = 55
    scout_min_desirability_score: int = 50
    scout_strong_qualification_score: int = 80
    scout_strong_desirability_score: int = 75
    scout_maybe_qualification_score: int = 40
    scout_maybe_desirability_score: int = 40

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in {"development", "dev", "local", "test"}


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="OPSPILOT_", extra="ignore")

    app_name: str = "OpsPilot"
    environment: str = "development"
    api_prefix: str = "/api/v1"

    database_url: str = "postgresql+psycopg://opspilot:opspilot@localhost:5438/opspilot"
    redis_url: str = "redis://localhost:6381/0"

    cors_origins: list[str] = ["http://localhost:3010"]


@lru_cache
def get_settings() -> Settings:
    return Settings()

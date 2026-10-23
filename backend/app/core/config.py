from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-insecure-secret-change-me-0123456789"


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="OPSPILOT_", extra="ignore")

    app_name: str = "OpsPilot"
    environment: str = "development"
    api_prefix: str = "/api/v1"

    database_url: str = "postgresql+psycopg://opspilot:opspilot@localhost:5438/opspilot"
    redis_url: str = "redis://localhost:6381/0"

    cors_origins: list[str] = ["http://localhost:3010"]

    # Authentication
    jwt_secret: str = DEV_JWT_SECRET
    jwt_issuer: str = "opspilot"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = False  # True behind HTTPS
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    @model_validator(mode="after")
    def _require_real_secret_outside_dev(self) -> "Settings":
        if self.environment not in {"development", "test"} and (
            self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32
        ):
            raise ValueError("OPSPILOT_JWT_SECRET must be set to a random value of at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

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

    # Observability
    log_format: str = "text"  # "text" | "json" (one JSON object per line, with request ids)
    log_level: str = "INFO"
    metrics_token: str | None = None  # if set, GET /metrics requires "Authorization: Bearer <token>"
    worker_metrics_port: int = 9101

    # Authentication
    jwt_secret: str = DEV_JWT_SECRET
    jwt_issuer: str = "opspilot"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = False  # True behind HTTPS
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    # Abuse controls (docs/security.md). 0 disables a limit.
    rate_limit_per_minute: int = 600  # every API call, per client IP
    login_rate_limit_per_minute: int = 20  # sign-in attempts per client IP, across all e-mails (credential stuffing)
    ai_rate_limit_per_minute: int = 20  # LLM-backed / expensive calls, per user
    max_body_bytes: int = 1_048_576
    # Proxies whose X-Forwarded-For is believed (CIDRs). Empty: the TCP peer is the client.
    trusted_proxies: list[str] = []

    # AI. Everything works with the defaults: deterministic embeddings and an extractive writer, no API keys needed.
    embedding_provider: str = "hashing"  # "hashing" | "fastembed"
    llm_provider: str = "none"  # "none" | "anthropic" (reads ANTHROPIC_API_KEY)
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"
    llm_timeout_seconds: float = 60.0

    @model_validator(mode="after")
    def _refuse_insecure_configuration(self) -> "Settings":
        if self.environment not in {"development", "test"} and (
            self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32
        ):
            raise ValueError("OPSPILOT_JWT_SECRET must be set to a random value of at least 32 characters")
        if "*" in self.cors_origins:
            raise ValueError("OPSPILOT_CORS_ORIGINS cannot contain '*': the API sends credentials (refresh cookie)")
        if self.environment == "production" and not self.cookie_secure:
            raise ValueError("OPSPILOT_COOKIE_SECURE must be true in production (the refresh cookie needs HTTPS)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

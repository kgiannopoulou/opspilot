import time
from pathlib import Path
from typing import Annotated, Any

import redis
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai.llm import llm_status
from app.api.router import api_router
from app.auth.rate_limit import get_redis
from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import register_error_handlers
from app.core.http_security import apply_http_security
from app.core.observability import configure_logging, metrics_response, observe_request, register_state_collector

settings = get_settings()
configure_logging(settings.log_format, settings.log_level)
register_state_collector()

app = FastAPI(
    title="OpsPilot API",
    description="AI-powered application support & incident intelligence.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
)


@app.middleware("http")
async def observability(request: Request, call_next: Any) -> Response:
    return await observe_request(request, call_next, settings.api_prefix)


@app.middleware("http")
async def http_security(request: Request, call_next: Any) -> Response:
    # Registered last = outermost: security headers are added to every response, including errors and 413s.
    return await apply_http_security(request, call_next, settings.max_body_bytes, hsts=settings.cookie_secure)


register_error_handlers(app)
app.include_router(api_router, prefix=settings.api_prefix)

# The migration this code expects: /ready refuses traffic until the database has been migrated to it.
_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
_alembic = Config()
_alembic.set_main_option("script_location", str(_MIGRATIONS))
EXPECTED_MIGRATION = ScriptDirectory.from_config(_alembic).get_current_head() if _MIGRATIONS.exists() else None


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """Liveness: the process is up. Deliberately checks nothing else, so a database outage never restarts the API."""
    return {"status": "ok", "service": "opspilot-api"}


@app.get("/ready", tags=["system"])
def ready(db: Annotated[Session, Depends(get_db)], client: Annotated[redis.Redis, Depends(get_redis)]) -> JSONResponse:
    """Readiness: can this instance serve traffic? PostgreSQL (at the expected migration) is required; Redis and the
    LLM are reported but optional, because the API degrades without them (docs/resilience.md)."""
    checks: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()
    try:
        version = db.scalar(text("SELECT version_num FROM alembic_version"))
        ok = EXPECTED_MIGRATION is None or version == EXPECTED_MIGRATION
        checks["database"] = {"ok": ok, "migration": version, "expected": EXPECTED_MIGRATION,
                              "latency_ms": round((time.perf_counter() - started) * 1000, 1)}  # fmt: skip
    except SQLAlchemyError as exc:
        checks["database"] = {"ok": False, "error": type(exc).__name__}
    started = time.perf_counter()
    try:
        client.ping()
        checks["redis"] = {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
    except redis.RedisError as exc:
        checks["redis"] = {"ok": False, "error": type(exc).__name__, "impact": "jobs run in-process; cache bypassed"}
    status = llm_status()
    checks["llm"] = {"ok": status["circuit"] != "open", "provider": status["provider"], "circuit": status["circuit"]}
    is_ready = checks["database"]["ok"]
    degraded = is_ready and not all(c["ok"] for c in checks.values())
    body = {"status": "degraded" if degraded else "ready" if is_ready else "not_ready", "checks": checks}
    return JSONResponse(body, status_code=200 if is_ready else 503)


@app.get("/metrics", tags=["system"], include_in_schema=False)
def metrics(authorization: Annotated[str | None, Header()] = None) -> Response:
    """Prometheus metrics. With OPSPILOT_METRICS_TOKEN set, scrapers must send it as a bearer token."""
    if settings.metrics_token and authorization != f"Bearer {settings.metrics_token}":
        raise HTTPException(401, "metrics token required", headers={"WWW-Authenticate": "Bearer"})
    return metrics_response()

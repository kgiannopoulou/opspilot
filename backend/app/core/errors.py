import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError

log = logging.getLogger(__name__)

# Fields whose submitted value is never echoed back in a 422 (FastAPI includes the rejected "input" by default).
SENSITIVE_FIELDS = {"password", "token", "refresh_token", "secret", "authorization"}


class DomainError(Exception):
    """Base for business-rule failures raised by services; mapped to HTTP responses in one place."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra  # machine-readable details, e.g. {"existing": "TCK-2026-00042"}


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"


class ValidationFailed(DomainError):
    status_code = 422
    code = "validation_failed"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for error in exc.errors():
            error = dict(error)
            if SENSITIVE_FIELDS & {str(part).lower() for part in error.get("loc", ())}:
                error.pop("input", None)
            errors.append(error)
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

    @app.exception_handler(DomainError)
    def handle_domain_error(_: Request, exc: DomainError) -> JSONResponse:
        content = {"error": exc.code, "detail": exc.message, **exc.extra}
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(OperationalError)
    @app.exception_handler(InterfaceError)
    def handle_database_unavailable(request: Request, exc: Exception) -> JSONResponse:
        # Connection refused, server restarting, failover: a clear, retryable 503 instead of a stack trace.
        log.error("database unavailable during %s %s: %s", request.method, request.url.path, type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"error": "database_unavailable", "detail": "The database is unavailable. Try again shortly."},
            headers={"Retry-After": "5"},
        )

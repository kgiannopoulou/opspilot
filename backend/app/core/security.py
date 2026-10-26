"""Password hashing and token primitives. No FastAPI or database imports — pure, unit-testable functions."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings

ALGORITHM = "HS256"
BCRYPT_ROUNDS = 12
# bcrypt only uses the first 72 bytes of a password, and bcrypt>=5 raises instead of silently truncating. New
# passwords are validated against this limit (schemas/auth.py); a longer one at sign-in can never match.
BCRYPT_MAX_BYTES = 72

# Compared against when the user does not exist, so login timing does not reveal which e-mails are registered.
_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt(BCRYPT_ROUNDS)).decode()


class InvalidTokenError(Exception):
    pass


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(BCRYPT_ROUNDS)).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    candidate = password_hash or _DUMMY_HASH
    secret = password.encode()
    too_long = len(secret) > BCRYPT_MAX_BYTES
    # Always run one bcrypt comparison, so neither unknown e-mails nor over-long input answer faster.
    matches = bcrypt.checkpw(secret[:BCRYPT_MAX_BYTES], candidate.encode())
    return matches and password_hash is not None and not too_long


def create_access_token(user_id: int, role: str, now: datetime | None = None) -> tuple[str, int]:
    """Short-lived JWT. Returns (token, lifetime in seconds)."""
    settings = get_settings()
    now = now or datetime.now(UTC)
    lifetime = timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": str(user_id),
        "role": role,
        "typ": "access",
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + lifetime,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), int(lifetime.total_seconds())


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],  # pinned: never let the token choose its algorithm
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    if payload.get("typ") != "access":
        raise InvalidTokenError("not an access token")
    return payload


def new_refresh_token() -> str:
    """Opaque random string; only its SHA-256 is stored, so a database leak does not leak usable tokens."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

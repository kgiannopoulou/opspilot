"""Abuse controls in Redis: failed-login throttling per (e-mail, client IP) and fixed-window request rate limits."""

import contextlib
import logging
import time
from functools import lru_cache

import redis
from fastapi import HTTPException, status
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.core.config import get_settings

log = logging.getLogger(__name__)


@lru_cache
def get_redis() -> redis.Redis:
    """Shared client for request paths. Everything Redis does here fails open, so a dead Redis must cost one short
    timeout per call — not the OS connect timeout, and not redis-py's default retries multiplying it."""
    return redis.Redis.from_url(
        get_settings().redis_url,
        socket_timeout=0.5,
        socket_connect_timeout=0.5,
        retry=Retry(NoBackoff(), 0),
        decode_responses=True,
    )


class LoginThrottle:
    def __init__(self, client: redis.Redis | None = None) -> None:
        settings = get_settings()
        self.client = client or get_redis()
        self.max_failures = settings.login_max_failures
        self.window_seconds = settings.login_lockout_minutes * 60

    @staticmethod
    def _key(email: str, ip: str) -> str:
        return f"login-failures:{email.lower()}:{ip}"

    def is_locked(self, email: str, ip: str) -> bool:
        try:
            return int(self.client.get(self._key(email, ip)) or 0) >= self.max_failures
        except redis.RedisError:
            # Fail open: an unavailable cache must not lock everybody out. bcrypt still slows brute force.
            log.warning("login throttle unavailable (redis error); allowing attempt")
            return False

    def register_failure(self, email: str, ip: str) -> None:
        key = self._key(email, ip)
        try:
            pipe = self.client.pipeline()
            pipe.incr(key)
            pipe.expire(key, self.window_seconds, nx=True)
            pipe.execute()
        except redis.RedisError:
            log.warning("login throttle unavailable (redis error); failure not recorded")

    def reset(self, email: str, ip: str) -> None:
        with contextlib.suppress(redis.RedisError):
            self.client.delete(self._key(email, ip))


def get_login_throttle() -> LoginThrottle:
    return LoginThrottle()


class RateLimiter:
    """Fixed-window request counters in Redis: at most `limit` hits per `window` seconds per key.

    Fixed windows allow a burst of up to 2 × limit across a window boundary; for abuse protection (not billing) that is
    an accepted trade for one INCR per request. Like the login throttle it fails open: Redis down means no limits,
    never an outage.
    """

    def __init__(self, client: redis.Redis | None = None) -> None:
        self.client = client or get_redis()

    def hit(self, key: str, limit: int, window: int, now: float | None = None) -> tuple[bool, int, int]:
        """Count one request. Returns (allowed, remaining, seconds until the window resets)."""
        now = time.time() if now is None else now
        bucket = int(now // window)
        reset_in = max(1, int((bucket + 1) * window - now))
        try:
            pipe = self.client.pipeline()
            pipe.incr(f"rate:{key}:{bucket}")
            pipe.expire(f"rate:{key}:{bucket}", window + 1, nx=True)
            count = int(pipe.execute()[0])
        except redis.RedisError:
            log.warning("rate limiter unavailable (redis error); allowing request")
            return True, limit, reset_in
        return count <= limit, max(0, limit - count), reset_in


def enforce(limiter: RateLimiter, key: str, limit: int, window: int = 60) -> None:
    allowed, remaining, reset_in = limiter.hit(key, limit, window)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate limit exceeded, slow down",
            headers={"Retry-After": str(reset_in), "X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": "0"},
        )

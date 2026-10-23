"""Failed-login throttling in Redis: N failures per (e-mail, client IP) within a window lock that pair out."""

import contextlib
import logging
from functools import lru_cache

import redis

from app.core.config import get_settings

log = logging.getLogger(__name__)


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url, socket_timeout=0.5, decode_responses=True)


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

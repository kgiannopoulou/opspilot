"""Tiny read-through JSON cache in Redis. Fails open: if Redis is down, callers just compute the value."""

import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

import redis

from app.auth.rate_limit import get_redis

log = logging.getLogger(__name__)


def cached_json(key: str, ttl_seconds: int, compute: Callable[[], Any], client: redis.Redis | None = None) -> Any:
    client = client or get_redis()
    try:
        hit = client.get(key)
        if hit is not None:
            return json.loads(hit)
    except redis.RedisError:
        log.warning("cache unavailable; computing %s", key)
        return compute()
    value = compute()
    with contextlib.suppress(redis.RedisError):
        client.set(key, json.dumps(value, default=str), ex=ttl_seconds)
    return value

"""Live investigation progress over Server-Sent Events (ADR 004).

The worker commits each pipeline stage to PostgreSQL (the source of truth) and then publishes the investigation id on
a Redis channel. An open SSE stream subscribes to that channel and re-reads the row whenever it hears about a change,
so the browser gets a push within milliseconds instead of polling every second. Redis only carries "something
changed": a lost message costs at most one `poll_seconds` of delay, because the stream also re-reads on a timer, and
with Redis down the stream degrades to that timer.
"""

import contextlib
import json
import logging
import time
from collections.abc import Callable, Iterator

import redis
from sqlalchemy.orm import Session

from app.auth.rate_limit import get_redis
from app.models import AIInvestigation
from app.models.enums import InvestigationStatus

log = logging.getLogger(__name__)

CHANNEL = "opspilot:investigation:{}"
FINISHED = {InvestigationStatus.COMPLETED, InvestigationStatus.FAILED}


def publish_investigation(run_id: int, client: redis.Redis | None = None) -> None:
    """Best effort, after the commit: a missed notification is covered by the stream's own re-read timer."""
    try:
        (client or get_redis()).publish(CHANNEL.format(run_id), "changed")
    except redis.RedisError:
        log.debug("live update for investigation %s not published (redis unavailable)", run_id)


def sse(event: str, data: str, event_id: int | None = None) -> str:
    lines = [f"event: {event}"] + ([f"id: {event_id}"] if event_id is not None else [])
    lines += [f"data: {line}" for line in data.splitlines() or [""]]
    return "\n".join(lines) + "\n\n"


def investigation_stream(
    db: Session,
    client: redis.Redis,
    run_id: int,
    render: Callable[[AIInvestigation], str],
    poll_seconds: float = 2.0,
    heartbeat_seconds: float = 15.0,
    max_seconds: float = 600.0,
) -> Iterator[str]:
    """`progress` events (the rendered investigation) on every change, then `done`. Comment lines keep idle proxies
    from closing the connection; after `max_seconds` the stream ends with `timeout` and the client reconnects."""
    pubsub = None
    try:
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(CHANNEL.format(run_id))
    except redis.RedisError:
        pubsub = None
    started = last_sent = time.monotonic()
    last_payload, sequence = None, 0
    try:
        yield "retry: 3000\n\n"  # EventSource-compatible reconnect delay
        while True:
            db.rollback()  # end the previous snapshot so this read sees the worker's latest commit
            run = db.get(AIInvestigation, run_id)
            if run is None:
                yield sse("error", json.dumps({"detail": "investigation not found"}))
                return
            payload = render(run)
            now = time.monotonic()
            if payload != last_payload:
                sequence += 1
                yield sse("progress", payload, sequence)
                last_payload, last_sent = payload, now
            if run.status in FINISHED:
                yield sse("done", json.dumps({"status": run.status.value}))
                return
            if now - started > max_seconds:
                yield sse("timeout", "{}")
                return
            if now - last_sent >= heartbeat_seconds:
                yield ": keep-alive\n\n"
                last_sent = now
            if pubsub is not None:
                try:
                    pubsub.get_message(timeout=poll_seconds)
                    continue
                except redis.RedisError:
                    pubsub = None
            time.sleep(poll_seconds / 2)
    finally:
        if pubsub is not None:
            with contextlib.suppress(redis.RedisError):
                pubsub.close()

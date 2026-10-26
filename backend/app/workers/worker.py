"""The worker loop: take jobs from the Redis stream and run them, one at a time per process.

Scale by running more processes (`docker compose up --scale worker=3`): the consumer group gives each message to
exactly one of them, and a message whose worker died is reclaimed after STALE_AFTER_MS.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid

import redis
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.workers.jobs import run_job
from app.workers.queue import STALE_AFTER_MS, JobQueue, Message

log = logging.getLogger(__name__)
DATABASE_DOWN = (OperationalError, InterfaceError)


class DatabaseUnavailable(Exception):
    """The job could not even be recorded: leave its message unacknowledged so it is retried, not lost."""


class Worker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        client: redis.Redis,
        name: str | None = None,
        stale_after_ms: int = STALE_AFTER_MS,
    ) -> None:
        self.session_factory = session_factory
        self.queue = JobQueue(client)
        self.name = name or os.environ.get("OPSPILOT_WORKER_NAME") or f"worker-{os.getpid()}"
        self.stale_after_ms = stale_after_ms

    def _process(self, message: Message) -> None:
        try:
            with self.session_factory() as db:
                status = run_job(db, self.queue, uuid.UUID(message.job_id), self.name)
            log.info("job %s %s → %s", message.job_id, message.type, status.value)
        except DATABASE_DOWN as exc:
            # Not acknowledged: the message stays pending and is reclaimed (by any worker) once the database is back.
            raise DatabaseUnavailable(str(exc)) from exc
        except Exception:  # noqa: BLE001 — never let one bad message stop the worker
            log.exception("job %s crashed the handler wrapper", message.job_id)
        self.queue.ack(message.id)

    def run_once(self, block_ms: int | None = None) -> int:
        """One polling cycle: promote due retries, take over stale messages, process new ones."""
        self.queue.promote_due()
        messages = self.queue.reclaim_stale(self.name, self.stale_after_ms) + self.queue.read(
            self.name, count=5, block_ms=block_ms
        )
        for message in messages:
            self._process(message)
        return len(messages)

    def drain(self, max_cycles: int = 50) -> int:
        """Process until the stream is empty (tests and one-off runs). Delayed retries are not waited for."""
        total = 0
        for _ in range(max_cycles):
            processed = self.run_once()
            total += processed
            if processed == 0:
                break
        return total

    def run_forever(self, stop: threading.Event, block_ms: int = 5_000) -> None:
        log.info("worker %s started", self.name)
        self.queue.ensure_group()
        while not stop.is_set():
            try:
                self.run_once(block_ms=block_ms)
            except redis.ConnectionError as exc:
                log.warning("redis unavailable (%s); retrying in 2 s", exc)
                stop.wait(2)
            except DatabaseUnavailable as exc:
                log.warning("database unavailable (%s); jobs stay queued, retrying in 5 s", exc)
                stop.wait(5)
        log.info("worker %s stopped", self.name)

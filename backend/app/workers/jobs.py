"""Job submission and the handlers that run in the worker.

Submitting writes the durable row first, then the Redis message. If Redis is down the API does not fail: the job
runs in-process after the response is sent (FastAPI BackgroundTasks), which is slower under load but keeps the
product working — the same "degrade, don't break" rule as the LLM fallback.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import redis
from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import AIInvestigation, BackgroundJob, Ticket
from app.models.enums import JobStatus
from app.services import audit
from app.services.classification import ClassificationService
from app.services.investigations import InvestigationService
from app.workers.queue import JobQueue

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 5  # 5 s, then 10 s


def triage_ticket(db: Session, job: BackgroundJob, final_attempt: bool) -> None:
    """Correlate signals around a new ticket and store an AI triage suggestion for an analyst to review."""
    ticket = db.get(Ticket, job.payload["ticket_id"])
    if ticket is None:
        return  # deleted meanwhile: nothing to do
    audit.record(db, None, "ticket.ai_triage_started", "ticket", ticket.number, {"job_id": str(job.id)})
    db.commit()
    ClassificationService(db).classify(ticket)


def investigate(db: Session, job: BackgroundJob, final_attempt: bool) -> None:
    run = db.get(AIInvestigation, job.payload["investigation_id"])
    if run is not None:
        InvestigationService(db).execute(run, final_attempt=final_attempt)


HANDLERS: dict[str, Callable[[Session, BackgroundJob, bool], None]] = {
    "ticket.triage": triage_ticket,
    "incident.investigate": investigate,
}


def run_job(db: Session, queue: JobQueue | None, job_id: uuid.UUID, worker: str) -> JobStatus:
    """Execute one job and record the outcome. Safe to call twice for the same job (finished jobs are skipped)."""
    job = db.get(BackgroundJob, job_id)
    if job is None or job.status in (JobStatus.SUCCEEDED, JobStatus.DEAD):
        return job.status if job else JobStatus.DEAD
    job.status, job.worker, job.started_at, job.error = JobStatus.RUNNING, worker, datetime.now(UTC), None
    job.attempts += 1
    db.commit()
    final_attempt = job.attempts >= MAX_ATTEMPTS or queue is None
    try:
        HANDLERS[job.type](db, job, final_attempt)
    except Exception as exc:  # noqa: BLE001 — recorded on the job; retried or dead-lettered
        db.rollback()
        job = db.get(BackgroundJob, job_id)
        job.error = f"{type(exc).__name__}: {exc}"[:2000]
        job.finished_at = datetime.now(UTC)
        if final_attempt:
            job.status = JobStatus.DEAD
            if queue is not None:
                queue.dead_letter(str(job.id), job.type, job.error)
        else:
            job.status = JobStatus.RETRYING
            queue.retry_later(str(job.id), job.type, BACKOFF_SECONDS * 2 ** (job.attempts - 1))
        log.warning("job %s (%s) attempt %s failed: %s", job.id, job.type, job.attempts, job.error)
        db.commit()
        return job.status
    job.status, job.finished_at = JobStatus.SUCCEEDED, datetime.now(UTC)
    db.commit()
    return job.status


class JobService:
    def __init__(self, db: Session, client: redis.Redis) -> None:
        self.db = db
        self.queue = JobQueue(client)

    def submit(
        self,
        type_: str,
        entity_type: str,
        entity_id: str | int,
        payload: dict[str, Any],
        background: BackgroundTasks | None = None,
    ) -> BackgroundJob:
        job = BackgroundJob(type=type_, entity_type=entity_type, entity_id=str(entity_id), payload=payload)
        self.db.add(job)
        self.db.commit()
        try:
            self.queue.enqueue(str(job.id), type_)
        except redis.RedisError as exc:
            log.warning("queue unavailable (%s); running %s %s in-process", exc, type_, job.id)
            if background is not None:
                factory = sessionmaker(bind=self.db.get_bind(), autoflush=False, expire_on_commit=False)
                background.add_task(_run_detached, factory, job.id)
        return job

    def recent(self, limit: int = 50) -> list[BackgroundJob]:
        return list(self.db.scalars(select(BackgroundJob).order_by(BackgroundJob.enqueued_at.desc()).limit(limit)))


def _run_detached(factory: sessionmaker[Session], job_id: uuid.UUID) -> None:
    with factory() as db:
        run_job(db, None, job_id, worker="api-fallback")

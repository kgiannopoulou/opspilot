"""Background jobs: Redis Streams queue semantics, retries, dead letters and the no-Redis fallback."""

from datetime import UTC, datetime

import pytest
import redis
from sqlalchemy import select

from app.ai.engine import InvestigationEngine
from app.auth.rate_limit import get_redis
from app.main import app
from app.models import AIInvestigation, BackgroundJob, Incident, TicketClassification, User
from app.models.enums import JobStatus
from app.scripts.seed import seed
from app.workers import jobs as jobs_module
from app.workers.queue import DEAD, DELAYED, JobQueue
from app.workers.worker import Worker
from tests.conftest import as_user


def test_stream_semantics(throttle):
    queue = JobQueue(throttle.client)
    queue.enqueue("job-1", "ticket.triage")
    first = queue.read("worker-a")
    assert [(m.job_id, m.type) for m in first] == [("job-1", "ticket.triage")]
    assert queue.read("worker-b") == []  # a message goes to one consumer only
    assert queue.stats()["in_progress"] == 1

    # worker-a dies without acking: after the idle time, another worker takes the message over.
    reclaimed = queue.reclaim_stale("worker-b", min_idle_ms=0)
    assert [m.job_id for m in reclaimed] == ["job-1"]
    queue.ack(reclaimed[0].id)
    assert queue.stats()["in_progress"] == 0


@pytest.fixture
def demo(db):
    seed(db, now=datetime.now(UTC))
    users = {u.email.split("@")[0]: u for u in db.query(User)}
    incident = db.scalar(select(Incident).where(Incident.title == "HTTP 500 on password reset"))
    return users, incident


def test_failed_job_is_retried_with_backoff_then_succeeds(client, db, demo, worker, throttle, monkeypatch):
    users, incident = demo
    original = InvestigationEngine.investigate
    calls = {"n": 0}

    def flaky(self, incident, step=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("database restarted")
        return original(self, incident, step)

    monkeypatch.setattr(InvestigationEngine, "investigate", flaky)
    monkeypatch.setattr(jobs_module, "BACKOFF_SECONDS", 0)
    url = f"/api/v1/incidents/{incident.number}/investigations"
    queued = client.post(url, headers=as_user(users["maria"])).json()

    worker.drain()  # attempt 1 fails → RETRYING; the retry is due immediately (backoff 0) and succeeds
    job = db.scalar(select(BackgroundJob).where(BackgroundJob.type == "incident.investigate"))
    db.refresh(job)
    assert job.status == JobStatus.SUCCEEDED and job.attempts == 2
    inv = client.get(f"{url}/{queued['id']}", headers=as_user(users["viewer"])).json()
    assert inv["status"] == "COMPLETED" and inv["attempts"] == 2 and inv["error"] is None


def test_job_out_of_attempts_goes_to_the_dead_letter_stream(client, db, demo, worker, throttle, monkeypatch):
    users, incident = demo

    def broken(self, incident, step=None):
        raise RuntimeError("engine bug")

    monkeypatch.setattr(InvestigationEngine, "investigate", broken)
    monkeypatch.setattr(jobs_module, "BACKOFF_SECONDS", 0)
    url = f"/api/v1/incidents/{incident.number}/investigations"
    queued = client.post(url, headers=as_user(users["maria"])).json()
    worker.drain()

    job = db.scalar(select(BackgroundJob).where(BackgroundJob.type == "incident.investigate"))
    db.refresh(job)
    assert job.status == JobStatus.DEAD and job.attempts == 3 and "engine bug" in job.error
    assert throttle.client.xlen(DEAD) == 1 and throttle.client.zcard(DELAYED) == 0
    inv = client.get(f"{url}/{queued['id']}", headers=as_user(users["viewer"])).json()
    assert inv["status"] == "FAILED" and inv["error"] == "RuntimeError: engine bug"
    assert any(s["status"] == "failed" for s in inv["progress"])

    # The failure is visible to admins.
    overview = client.get("/api/v1/jobs", headers=as_user(users["nikos"])).json()
    assert overview["queue"]["available"] and overview["queue"]["dead"] == 1
    assert overview["by_status"]["DEAD"] == 1 and overview["recent"][0]["status"] == "DEAD"
    assert client.get("/api/v1/jobs", headers=as_user(users["maria"])).status_code == 403


def test_finished_jobs_are_not_run_twice(db, demo, session_factory, throttle):
    users, incident = demo
    with session_factory() as other:
        job = jobs_module.JobService(other, throttle.client).submit("ticket.triage", "ticket", "x", {"ticket_id": 0})
    worker = Worker(session_factory, throttle.client)
    assert worker.drain() == 1
    JobQueue(throttle.client).enqueue(str(job.id), job.type)  # duplicate delivery
    assert worker.drain() == 1
    db.expire_all()
    assert db.get(BackgroundJob, job.id).attempts == 1


def test_without_redis_jobs_run_in_process(client, db, demo):
    users, _ = demo
    app.dependency_overrides[get_redis] = lambda: redis.Redis(port=1, socket_connect_timeout=0.2)
    response = client.post(
        "/api/v1/tickets",
        json={"title": "Customers cannot complete checkout", "description": "The payment page freezes."},
        headers=as_user(users["sam"]),
    )
    assert response.status_code == 201  # the API does not fail because the queue is down
    job = db.scalar(select(BackgroundJob).where(BackgroundJob.entity_id == response.json()["number"]))
    db.refresh(job)
    assert job.status == JobStatus.SUCCEEDED and job.worker == "api-fallback"
    assert db.scalar(select(TicketClassification).where(TicketClassification.ticket_id == response.json()["id"]))


def test_seeded_investigations_report_every_stage(db, demo):
    for run in db.scalars(select(AIInvestigation)):
        assert [s["key"] for s in run.progress] == ["signals", "hypotheses", "documents", "write", "queries", "save"]
        assert all(s["status"] == "done" and s["started_at"] for s in run.progress)

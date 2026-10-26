"""Failure testing: what OpsPilot does when its dependencies fail.

The rule everywhere is "degrade, don't break": losing Redis, the LLM or the knowledge base costs speed or polish, never
the ability to work an incident. Losing PostgreSQL (the system of record) is answered with a clear, retryable 503 —
and queued AI work is kept, not dropped. The matrix with the user-visible behaviour is in docs/resilience.md.
"""

import threading
import types
import uuid

import pytest
import redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.ai.llm import CircuitBreaker, ClaudeClient, GuardedLLM, LLMError
from app.ai.writer import LLMWriter
from app.auth.rate_limit import LoginThrottle, get_login_throttle, get_redis
from app.core.database import get_db
from app.main import app
from app.models import BackgroundJob, DocumentChunk, KnowledgeDocument, Ticket, TicketClassification
from app.models.enums import JobStatus
from app.schemas.ticket import TicketCreate
from app.services.investigations import InvestigationService
from app.services.tickets import TicketService
from app.workers.queue import JobQueue
from app.workers.worker import DatabaseUnavailable, Worker
from tests.conftest import PASSWORD, as_user

DEAD_DB = "postgresql+psycopg://opspilot:opspilot@127.0.0.1:1/opspilot?connect_timeout=1"


def dead_redis() -> redis.Redis:
    """Configured like the production client (get_redis): short timeouts, no automatic retries."""
    return redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2, socket_timeout=0.2,
                       retry=Retry(NoBackoff(), 0), decode_responses=True)  # fmt: skip


@pytest.fixture
def dead_db_factory():
    engine = create_engine(DEAD_DB, pool_pre_ping=False)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


# -- PostgreSQL unavailable ------------------------------------------------------------------------------------


def test_database_down_is_a_clear_retryable_503(client, seeded, dead_db_factory):
    def broken():
        session = dead_db_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = broken
    response = client.get("/api/v1/tickets", headers=as_user(seeded["maria"]))
    assert response.status_code == 503 and response.headers["Retry-After"] == "5"
    assert response.json() == {"error": "database_unavailable",
                               "detail": "The database is unavailable. Try again shortly."}  # fmt: skip
    assert "Traceback" not in response.text and "psycopg" not in response.text  # nothing internal leaks


def test_worker_keeps_jobs_while_the_database_is_down(client, db, seeded, throttle, session_factory, dead_db_factory):
    body = {"title": "Customers cannot pay", "description": "Checkout freezes"}
    ticket = client.post("/api/v1/tickets", headers=as_user(seeded["agent"]), json=body).json()

    with pytest.raises(DatabaseUnavailable):
        Worker(dead_db_factory, throttle.client, name="worker-db-down").run_once()
    assert throttle.client.xpending("opspilot:jobs", "workers")["pending"] == 1  # not acknowledged, not lost

    # The database is back: the pending message is reclaimed and the job runs exactly once.
    assert Worker(session_factory, throttle.client, name="worker-2", stale_after_ms=0).drain() == 1
    job = db.scalar(select(BackgroundJob).where(BackgroundJob.entity_id == ticket["number"]))
    db.refresh(job)
    assert (job.status, job.attempts, job.worker) == (JobStatus.SUCCEEDED, 1, "worker-2")
    assert throttle.client.xpending("opspilot:jobs", "workers")["pending"] == 0


# -- worker crashes ----------------------------------------------------------------------------------------------


def test_a_crashed_workers_job_is_taken_over(client, db, seeded, throttle, session_factory):
    body = {"title": "Customers cannot pay", "description": "Checkout freezes"}
    ticket = client.post("/api/v1/tickets", headers=as_user(seeded["agent"]), json=body).json()
    queue = JobQueue(throttle.client)

    # worker-1 takes the message, marks the job RUNNING… and dies (no ack, no result).
    [message] = queue.read("worker-1", count=1)
    job = db.get(BackgroundJob, uuid.UUID(message.job_id))
    job.status, job.worker, job.attempts = JobStatus.RUNNING, "worker-1", 1
    db.commit()

    # worker-2 claims messages idle longer than its threshold (60 s in production; 0 here).
    assert Worker(session_factory, throttle.client, name="worker-2", stale_after_ms=0).drain() == 1
    db.refresh(job)
    assert (job.status, job.worker, job.attempts) == (JobStatus.SUCCEEDED, "worker-2", 2)
    suggestions = db.scalar(select(func.count()).select_from(TicketClassification).where(
        TicketClassification.ticket_id == ticket["id"]))  # fmt: skip
    assert suggestions == 1


# -- Redis unavailable -------------------------------------------------------------------------------------------


def test_everything_still_works_without_redis(client, db, demo):
    users, incidents = demo
    app.dependency_overrides[get_redis] = dead_redis
    app.dependency_overrides[get_login_throttle] = lambda: LoginThrottle(dead_redis())
    maria = as_user(users["maria"])

    login = client.post("/api/v1/auth/login", json={"email": "maria@opspilot.dev", "password": "opspilot-demo"})
    assert login.status_code == 200  # the brute-force throttle fails open; bcrypt still slows guessing
    assert client.get("/api/v1/dashboard/summary", headers=maria).status_code == 200  # cache fails open

    ticket = client.post("/api/v1/tickets", headers=maria, json={"title": "Pay fails", "description": "Errors"})
    assert ticket.status_code == 201
    number = incidents["HTTP 500 on password reset"]
    queued = client.post(f"/api/v1/incidents/{number}/investigations", headers=maria)
    assert queued.status_code == 202  # ran in-process after the response
    run = client.get(f"/api/v1/incidents/{number}/investigations/{queued.json()['id']}", headers=maria).json()
    assert run["status"] == "COMPLETED"

    status = client.get("/api/v1/ai/status", headers=maria).json()
    assert status["queue_available"] is False and status["degraded"] is True
    assert "job queue is unavailable" in status["message"]
    assert client.get("/api/v1/jobs", headers=as_user(users["nikos"])).json()["queue"]["available"] is False


def test_login_with_redis_down_is_still_checked(client, seeded):
    app.dependency_overrides[get_login_throttle] = lambda: LoginThrottle(dead_redis())
    bad = client.post("/api/v1/auth/login", json={"email": "maria@opspilot.dev", "password": "wrong-password"})
    assert bad.status_code == 401
    good = client.post("/api/v1/auth/login", json={"email": "maria@opspilot.dev", "password": PASSWORD})
    assert good.status_code == 200


# -- LLM failures ------------------------------------------------------------------------------------------------


class ScriptedLLM:
    name = "scripted"

    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def complete_json(self, system, prompt, schema, max_tokens=4096):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else self.outcomes_default
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    outcomes_default = TimeoutError("read timed out")


def test_llm_timeout_falls_back_to_the_extractive_writer(db, demo):
    _, incidents = demo
    from app.models import Incident

    incident = db.scalar(select(Incident).where(Incident.number == incidents["HTTP 500 on password reset"]))
    writer = LLMWriter(GuardedLLM(ScriptedLLM(TimeoutError("read timed out"))))
    run = InvestigationService(db, writer=writer).run(incident, None)
    assert run.status.value == "COMPLETED" and run.engine.endswith("extractive-v1")
    assert run.grounding["passed"] and run.grounding["llm_error"] == "model call failed: TimeoutError"


def test_circuit_breaker_stops_calling_a_failing_provider():
    now = [0.0]
    llm = ScriptedLLM()  # fails every time
    guarded = GuardedLLM(llm, CircuitBreaker(threshold=3, cooldown=60, clock=lambda: now[0]))
    for _ in range(3):
        with pytest.raises(LLMError, match="TimeoutError"):
            guarded.complete_json("s", "p", {})
    assert guarded.breaker.state().state == "open"

    for _ in range(10):  # an outage: fail fast, the provider is not called again
        with pytest.raises(LLMError, match="circuit open"):
            guarded.complete_json("s", "p", {})
    assert llm.calls == 3

    now[0] = 61  # cooldown over: one trial call; it fails, so the circuit opens again
    assert guarded.breaker.state().state == "half_open"
    with pytest.raises(LLMError, match="TimeoutError"):
        guarded.complete_json("s", "p", {})
    assert llm.calls == 4 and guarded.breaker.state().state == "open"

    now[0] = 130  # provider recovered: the trial succeeds and the circuit closes
    llm.outcomes = [{"ok": True}]
    assert guarded.complete_json("s", "p", {}) == {"ok": True}
    assert guarded.breaker.state().state == "closed" and guarded.breaker.state().consecutive_failures == 0


def _sdk_returning(text_: str | None, stop_reason: str = "end_turn"):
    """A stand-in for the Anthropic SDK client returning one message."""
    blocks = [] if text_ is None else [types.SimpleNamespace(type="text", text=text_)]
    response = types.SimpleNamespace(stop_reason=stop_reason, content=blocks, _request_id="req_test", usage={})
    messages = types.SimpleNamespace(create=lambda **_: response)
    return types.SimpleNamespace(beta=types.SimpleNamespace(messages=messages))


@pytest.mark.parametrize(
    ("text_", "stop_reason", "error"),
    [
        ('{"root_cause": "x", "narrative": ', "end_turn", "invalid JSON"),
        ("Sure! Here is the JSON you asked for.", "end_turn", "invalid JSON"),
        ('{"root_cause": "x"', "max_tokens", "truncated"),
        (None, "end_turn", "no text block"),
        ("{}", "refusal", "declined"),
    ],
)
def test_malformed_model_output_is_an_llm_error(text_, stop_reason, error):
    client = ClaudeClient("claude-test", "low", 5, client=_sdk_returning(text_, stop_reason))
    with pytest.raises(LLMError, match=error):
        client.complete_json("system", "prompt", {"type": "object"})


def test_schema_valid_but_wrong_draft_is_rejected(db, demo):
    """Malformed in meaning rather than syntax: the draft cites evidence that does not exist → extractive is shown."""
    _, incidents = demo
    from app.models import Incident

    incident = db.scalar(select(Incident).where(Incident.number == incidents["HTTP 500 on password reset"]))
    draft = {"root_cause": "The SMTP relay is down.", "narrative": "The relay was down for 3 hours [LOG-1].",
             "recommended_steps": ["Restart the relay"]}  # fmt: skip
    run = InvestigationService(db, writer=LLMWriter(GuardedLLM(ScriptedLLM(draft)))).run(incident, None)
    assert run.engine.endswith("extractive-v1") and run.grounding["passed"]
    assert run.grounding["rejected_llm_draft"]["unknown_refs"] == ["LOG-1"]


# -- vector search returns nothing -------------------------------------------------------------------------------


def test_investigation_without_any_knowledge_base(client, db, demo):
    users, incidents = demo
    db.execute(text("DELETE FROM document_chunks"))
    db.execute(text("DELETE FROM knowledge_documents"))
    db.commit()
    assert db.scalar(select(func.count()).select_from(KnowledgeDocument)) == 0
    maria = as_user(users["maria"])

    hits = client.get("/api/v1/knowledge/search", params={"q": "gateway timeout"}, headers=maria).json()
    assert hits["hits"] == []

    from app.models import Incident

    incident = db.scalar(select(Incident).where(Incident.number == incidents["Payment checkout timeout"]))
    run = InvestigationService(db).run(incident, None)
    assert run.status.value == "COMPLETED" and run.hypothesis_key == "payment_gateway_timeout"
    assert [e for e in run.evidence if e.kind.value == "DOCUMENT"] == []
    assert run.grounding["passed"] and db.scalar(select(func.count()).select_from(DocumentChunk)) == 0


# -- duplicate tickets -------------------------------------------------------------------------------------------


def test_idempotency_key_makes_retries_safe(client, db, seeded):
    agent = as_user(seeded["agent"])
    body = {"title": "Payment fails at checkout", "description": "Merchant m_412: 4 of 10 orders failed"}
    first = client.post("/api/v1/tickets", headers={**agent, "Idempotency-Key": "retry-abc"}, json=body)
    again = client.post("/api/v1/tickets", headers={**agent, "Idempotency-Key": "retry-abc"}, json=body)
    assert first.status_code == 201 and again.status_code == 200
    assert again.headers["Idempotent-Replayed"] == "true" and again.json()["number"] == first.json()["number"]
    assert db.scalar(select(func.count()).select_from(Ticket)) == 1
    assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 1  # triage queued once

    reused = client.post("/api/v1/tickets", headers={**agent, "Idempotency-Key": "retry-abc"},
                         json={**body, "title": "Something else entirely"})  # fmt: skip
    assert reused.status_code == 422


def test_the_same_report_twice_is_flagged_as_a_duplicate(client, seeded):
    agent = as_user(seeded["agent"])
    body = {"title": "Payment fails at checkout", "description": "Merchant m_412: 4 of 10 orders failed"}
    first = client.post("/api/v1/tickets", headers=agent, json=body).json()
    # Same text with different spacing/case is still the same report.
    dup = client.post("/api/v1/tickets", headers=agent,
                      json={"title": "payment  fails at CHECKOUT", "description": body["description"]})  # fmt: skip
    assert dup.status_code == 409 and dup.json()["existing"] == first["number"]
    assert (
        client.post("/api/v1/tickets", headers=agent, json=body, params={"allow_duplicate": "true"}).status_code == 201
    )
    # Someone else reporting the same thing is not a duplicate submission — it is another affected customer.
    assert client.post("/api/v1/tickets", headers=as_user(seeded["maria"]), json=body).status_code == 201


def test_concurrent_requests_with_one_key_create_one_ticket(db, seeded, session_factory):
    data = TicketCreate(title="Checkout broken", description="Pay button errors")
    results, barrier = [], threading.Barrier(4)

    def submit():
        with session_factory() as session:
            barrier.wait()
            ticket, created = TicketService(session).submit(data, session.merge(seeded["agent"]), "same-key")
            results.append((ticket.number, created))

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({number for number, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    assert db.scalar(select(func.count()).select_from(Ticket)) == 1

"""Health, readiness, metrics, request ids and structured logs."""

import json
import logging
import types

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.ai.llm import CircuitBreaker, ClaudeClient, GuardedLLM, LLMError
from app.auth.rate_limit import get_redis
from app.core.database import get_db
from app.core.observability import REGISTRY, JsonFormatter, StateCollector, request_id_var
from app.main import app
from app.models import Incident
from app.services.investigations import InvestigationService
from tests.conftest import as_user
from tests.test_failures import DEAD_DB, dead_redis


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_liveness_checks_nothing_else(client):
    assert client.get("/health").json() == {"status": "ok", "service": "opspilot-api"}


def test_ready_when_database_is_migrated(client, seeded):
    body = client.get("/ready").json()
    assert body["status"] == "ready"
    assert body["checks"]["database"]["ok"] and body["checks"]["database"]["migration"] == main.EXPECTED_MIGRATION
    assert body["checks"]["redis"]["ok"] and body["checks"]["llm"]["provider"] == "none"


def test_ready_but_degraded_without_redis(client, seeded):
    app.dependency_overrides[get_redis] = dead_redis
    response = client.get("/ready")
    assert response.status_code == 200 and response.json()["status"] == "degraded"
    assert response.json()["checks"]["redis"]["impact"] == "jobs run in-process; cache bypassed"


def test_not_ready_without_database_or_with_pending_migrations(client, seeded, monkeypatch):
    monkeypatch.setattr(main, "EXPECTED_MIGRATION", "9999")
    assert client.get("/ready").status_code == 503  # new code, old schema: keep it out of the load balancer
    monkeypatch.undo()

    engine = create_engine(DEAD_DB)
    factory = sessionmaker(bind=engine)
    app.dependency_overrides[get_db] = lambda: factory()
    response = client.get("/ready")
    assert response.status_code == 503 and response.json()["status"] == "not_ready"
    assert response.json()["checks"]["database"] == {"ok": False, "error": "OperationalError"}
    engine.dispose()


def test_request_ids_are_echoed_or_generated(client):
    generated = client.get("/health").headers["X-Request-ID"]
    assert len(generated) == 32 and int(generated, 16) >= 0
    assert client.get("/health", headers={"X-Request-ID": "trace-1234abcd"}).headers["X-Request-ID"] == "trace-1234abcd"
    injected = client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"}).headers["X-Request-ID"]
    assert injected != "bad id\nwith newline" and len(injected) == 32  # never reflect arbitrary input into logs


def test_http_metrics_use_route_templates(client, seeded):
    route = "/api/v1/tickets/{number}"
    before = sample("opspilot_http_requests_total", method="GET", route=route, status="404")
    for n in range(3):
        client.get(f"/api/v1/tickets/TCK-{n}", headers=as_user(seeded["viewer"]))
    client.get("/totally/unknown/path")
    text_ = client.get("/metrics").text
    assert sample("opspilot_http_requests_total", method="GET", route=route, status="404") == before + 3
    assert sample("opspilot_http_requests_total", method="GET", route="unmatched", status="404") >= 1
    assert "TCK-1" not in text_  # labels are templates, never raw ids
    assert sample("opspilot_http_request_duration_seconds_count", method="GET", route=route) >= 3


def test_metrics_can_require_a_token(client, monkeypatch):
    monkeypatch.setattr(main.settings, "metrics_token", "s3cret-scraper-token")
    assert client.get("/metrics").status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer s3cret-scraper-token"})
    assert ok.status_code == 200 and "opspilot_http_requests_total" in ok.text


def test_state_gauges_come_from_postgres_and_redis(demo, session_factory, throttle):
    families = {f.name: f for f in StateCollector(session_factory, lambda: throttle.client).collect()}
    values = {f.name: {tuple(s.labels.values()): s.value for s in f.samples} for f in families.values()}
    assert values["opspilot_dependency_up"] == {("redis",): 1.0, ("postgres",): 1.0}
    assert values["opspilot_incidents_active"] == {("SEV1",): 1.0, ("SEV2",): 2.0}
    assert sum(values["opspilot_tickets_open"].values()) > 0
    assert values["opspilot_incident_mttr_seconds"][()] > 600
    assert set(values["opspilot_queue_messages"]) == {("waiting",), ("in_progress",), ("delayed",), ("dead",)}
    assert values["opspilot_llm_circuit_open"][()] == 0.0


def test_state_gauges_report_a_dead_dependency(session_factory):
    families = {f.name: f for f in StateCollector(sessionmaker(bind=create_engine(DEAD_DB)), dead_redis).collect()}
    up = {s.labels["dependency"]: s.value for s in families["opspilot_dependency_up"].samples}
    assert up == {"redis": 0.0, "postgres": 0.0}  # the scrape still succeeds and says what is down


def test_ai_pipeline_metrics(db, demo):
    _, incidents = demo
    incident = db.query(Incident).filter(Incident.number == incidents["HTTP 500 on password reset"]).one()
    retrievals = sample("opspilot_rag_retrieval_duration_seconds_count")
    completed = sample("opspilot_investigations_total", outcome="completed", hypothesis="smtp_authentication_failure")
    passed = sample("opspilot_writeups_total", writer="extractive-v1", grounding="passed")

    InvestigationService(db).run(incident, None)
    assert sample("opspilot_rag_retrieval_duration_seconds_count") == retrievals + 1
    assert sample("opspilot_investigations_total", outcome="completed",
                  hypothesis="smtp_authentication_failure") == completed + 1  # fmt: skip
    assert sample("opspilot_writeups_total", writer="extractive-v1", grounding="passed") == passed + 1
    assert sample("opspilot_investigation_duration_seconds_count", outcome="completed") >= 1


def test_llm_latency_and_token_usage(monkeypatch):
    usage = types.SimpleNamespace(input_tokens=1200, output_tokens=340)
    response = types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text="{}")],
                                     _request_id="req", usage=usage)  # fmt: skip
    sdk = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **_: response)))
    before_in = sample("opspilot_llm_tokens_total", model="claude-metrics", direction="input")
    ClaudeClient("claude-metrics", "low", 5, client=sdk).complete_json("s", "p", {})
    assert sample("opspilot_llm_tokens_total", model="claude-metrics", direction="input") == before_in + 1200
    assert sample("opspilot_llm_tokens_total", model="claude-metrics", direction="output") >= 340
    assert sample("opspilot_llm_request_duration_seconds_count", model="claude-metrics", outcome="ok") >= 1

    class Down:
        name = "claude-down"

        def complete_json(self, *_a, **_k):
            raise LLMError("model provider unreachable")

    guarded = GuardedLLM(Down(), CircuitBreaker(threshold=1))
    with pytest.raises(LLMError):
        guarded.complete_json("s", "p", {})
    with pytest.raises(LLMError, match="circuit open"):
        guarded.complete_json("s", "p", {})
    assert sample("opspilot_llm_request_duration_seconds_count", model="claude-down", outcome="circuit_open") == 1


def test_job_metrics(client, seeded, worker):
    before = sample("opspilot_jobs_total", type="ticket.triage", outcome="succeeded")
    created = sample("opspilot_tickets_created_total", priority="P2")
    body = {"title": "Customers cannot pay", "description": "Checkout freezes", "priority": "P2"}
    client.post("/api/v1/tickets", headers=as_user(seeded["agent"]), json=body)
    worker.drain()
    assert sample("opspilot_jobs_total", type="ticket.triage", outcome="succeeded") == before + 1
    assert sample("opspilot_tickets_created_total", priority="P2") == created + 1


def test_json_logs_carry_the_request_id():
    token = request_id_var.set("req-abc12345")
    try:
        record = logging.LogRecord("opspilot.http", logging.INFO, __file__, 1, "request", (), None)
        record.route, record.status = "/api/v1/tickets", 200
        entry = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert entry["request_id"] == "req-abc12345" and entry["route"] == "/api/v1/tickets" and entry["status"] == 200
    assert entry["level"] == "INFO" and entry["message"] == "request"

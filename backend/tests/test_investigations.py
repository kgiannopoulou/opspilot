from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.ai.engine import Evidence, score
from app.models import AuditEvent, Incident, KnowledgeDocument, User
from app.models.enums import EvidenceKind, EvidenceStance, Severity
from app.scripts.seed import seed
from app.services.incidents import IncidentService
from app.services.knowledge import load_knowledge_base
from tests.conftest import as_user


@pytest.fixture
def demo(db):
    seed(db, now=datetime.now(UTC))
    users = {u.email.split("@")[0]: u for u in db.query(User)}
    incidents = {i.title: i.number for i in db.scalars(select(Incident)).unique()}
    return users, incidents


def by_kind(investigation: dict, kind: str) -> list[dict]:
    return [e for e in investigation["evidence"] if e["kind"] == kind]


def test_score_compounds_support_and_penalises_contradictions():
    supports = [Evidence(EvidenceKind.LOG_PATTERN, EvidenceStance.SUPPORTS, "", 0.5)] * 2
    assert score(supports) == 0.75
    against = Evidence(EvidenceKind.DEPENDENCY_HEALTH, EvidenceStance.CONTRADICTS, "", 0.2)
    assert score([*supports, against]) == 0.6
    assert score([Evidence(EvidenceKind.DOCUMENT, EvidenceStance.CONTEXT, "")]) == 0
    assert score([Evidence(EvidenceKind.LOG_PATTERN, EvidenceStance.SUPPORTS, "", 0.99)] * 3) == 0.97  # capped


def test_seeded_payment_incident_page(client, demo):
    users, incidents = demo
    number = incidents["Payment checkout timeout"]
    detail = client.get(f"/api/v1/incidents/{number}", headers=as_user(users["viewer"])).json()

    assert detail["severity"] == "SEV1" and detail["status"] == "INVESTIGATING"
    assert len(detail["tickets"]) == 2 and detail["ticket_count"] == 2
    assert detail["allowed_transitions"] == ["IDENTIFIED", "MONITORING", "RESOLVED"]

    inv = detail["latest_investigation"]
    assert inv["status"] == "COMPLETED" and inv["engine"] == "rules-v1+rag/extractive-v1"
    assert inv["hypothesis_key"] == "payment_gateway_timeout"
    assert 0.7 <= inv["confidence"] <= 0.97
    assert "5000ms" in inv["root_cause"] and "stripe" in inv["root_cause"]
    assert "FROM payments" in inv["suggested_sql"]

    log = by_kind(inv, "LOG_PATTERN")[0]
    assert log["stance"] == "SUPPORTS" and log["data"]["baseline_count"] == 0 and log["data"]["count"] > 20
    latency = next(e for e in by_kind(inv, "METRIC_DELTA") if e["data"]["metric"] == "api_latency_p95_ms")
    assert latency["data"]["service"] == "payment-service" and latency["data"]["change_pct"] > 300
    # The database is healthy, which localises the fault to the gateway call.
    db_health = by_kind(inv, "DEPENDENCY_HEALTH")[0]
    assert db_health["stance"] == "SUPPORTS" and db_health["data"]["service"] == "postgres-primary"
    similar = by_kind(inv, "SIMILAR_INCIDENT")[0]
    assert similar["related_incident"]["title"] == "Checkout payments failing — gateway timeouts"
    docs = by_kind(inv, "DOCUMENT")
    assert docs[0]["document"]["slug"] == "payment-gateway-timeout"
    assert docs[0]["data"]["section"] and docs[0]["data"]["excerpt"]  # a citable passage, not just a title
    assert "GatewayTimeoutException" in inv["search_query"]
    # Every sentence of the write-up cites evidence of this run, and the check that proves it is stored.
    assert inv["grounding"]["passed"] is True
    assert f"[{log['ref']}]" in inv["narrative"] and log["ref"].startswith("LOG-")
    assert docs[0]["ref"].startswith("RUNBOOK-payment-gateway-timeout §")
    assert not [e for e in inv["evidence"] if e["stance"] == "CONTRADICTS"]


def test_long_running_incident_is_not_polluted_by_later_incidents(client, demo):
    users, incidents = demo
    number = incidents["Merchant dashboard extremely slow"]
    inv = client.get(f"/api/v1/incidents/{number}", headers=as_user(users["viewer"])).json()["latest_investigation"]
    assert inv["hypothesis_key"] == "db_connection_pool_exhaustion" and inv["confidence"] > 0.8
    change = by_kind(inv, "CHANGE_EVENT")[0]
    assert change["stance"] == "SUPPORTS" and change["data"]["attributes"]["job"] == "merchant_revenue_report"
    implicated = {e["data"]["service"] for e in by_kind(inv, "METRIC_DELTA") if e["stance"] == "SUPPORTS"}
    assert "postgres-primary" in implicated


def test_run_investigation_requires_permission_and_is_audited(client, db, demo):
    users, incidents = demo
    number = incidents["HTTP 500 on password reset"]
    url = f"/api/v1/incidents/{number}/investigations"

    assert client.post(url, headers=as_user(users["sam"])).status_code == 403  # support agents cannot investigate
    assert (
        client.get(f"/api/v1/incidents/{number}", headers=as_user(users["sam"])).json()["latest_investigation"] is None
    )

    response = client.post(url, headers=as_user(users["maria"]))
    assert response.status_code == 201
    inv = response.json()
    assert inv["hypothesis_key"] == "smtp_authentication_failure"
    assert inv["requested_by"]["email"] == "maria@opspilot.dev"
    change = by_kind(inv, "CHANGE_EVENT")[0]
    assert change["stance"] == "SUPPORTS" and "smtp_credentials" in change["summary"]

    history = client.get(url, headers=as_user(users["viewer"])).json()
    assert [h["id"] for h in history] == [inv["id"]]
    assert db.scalar(
        select(AuditEvent).where(AuditEvent.action == "incident.investigated", AuditEvent.entity_id == number)
    )


def test_unknown_failure_mode_gets_low_confidence(client, db, demo):
    users, _ = demo
    incident = IncidentService(db).declare(
        title="Intermittent login slowness",
        severity=Severity.SEV3,
        primary_service="auth-service",
        started_at=datetime.now(UTC) - timedelta(hours=5),  # a quiet period: no injected failures yet
        actor=users["maria"],
    )
    inv = client.post(f"/api/v1/incidents/{incident.number}/investigations", headers=as_user(users["maria"])).json()
    assert inv["hypothesis_key"] == "unknown"
    assert inv["confidence"] <= 0.35
    assert "No anomalous log patterns" in inv["root_cause"]
    assert inv["recommended_steps"]


def test_incident_lifecycle(client, demo):
    users, incidents = demo
    number = incidents["Payment checkout timeout"]
    url = f"/api/v1/incidents/{number}/status"
    engineer = as_user(users["maria"])

    assert client.post(url, json={"status": "RESOLVED"}, headers=as_user(users["sam"])).status_code == 403
    assert client.post(url, json={"status": "RESOLVED"}, headers=engineer).status_code == 422  # no root cause yet

    identified = client.post(
        url, json={"status": "IDENTIFIED", "root_cause": "Gateway latency above the 5 s timeout."}, headers=engineer
    ).json()
    assert identified["status"] == "IDENTIFIED" and identified["root_cause"].startswith("Gateway latency")

    resolved = client.post(url, json={"status": "RESOLVED"}, headers=engineer).json()
    assert resolved["status"] == "RESOLVED" and resolved["resolved_at"] is not None
    assert client.post(url, json={"status": "MONITORING"}, headers=engineer).status_code == 409
    reopened = client.post(url, json={"status": "INVESTIGATING"}, headers=engineer).json()
    assert reopened["resolved_at"] is None


def test_incident_telemetry_and_logs(client, demo):
    users, incidents = demo
    number = incidents["Payment checkout timeout"]
    headers = as_user(users["viewer"])

    telemetry = client.get(f"/api/v1/incidents/{number}/telemetry", headers=headers).json()
    series = {(s["service"], s["metric"]): s["points"] for s in telemetry["series"]}
    latency = series[("payment-service", "api_latency_p95_ms")]
    assert len(latency) >= 90  # an hour before onset + the incident so far, per minute
    assert latency[-1]["value"] > 4 * latency[0]["value"]

    logs = client.get(f"/api/v1/incidents/{number}/logs", params={"limit": 20}, headers=headers).json()
    assert len(logs) == 20
    assert any(line["message"] == "GatewayTimeoutException" for line in logs)
    assert client.get("/api/v1/incidents/INC-0/logs", headers=headers).status_code == 404


def test_knowledge_base_loading_is_idempotent(client, db, demo):
    users, _ = demo
    assert load_knowledge_base(db) == {"created": 0, "updated": 0, "unchanged": 12, "reindexed": 0, "chunks": 0}
    assert db.query(KnowledgeDocument).count() == 12

    docs = client.get("/api/v1/knowledge", headers=as_user(users["viewer"])).json()
    runbook = next(d for d in docs if d["slug"] == "payment-gateway-timeout")
    assert runbook["category"] == "runbook" and runbook["service"]["name"] == "payment-service"
    assert "GatewayTimeoutException" in runbook["tags"]

    full = client.get("/api/v1/knowledge/payment-gateway-timeout", headers=as_user(users["viewer"])).json()
    assert full["content"].startswith("# Payment Gateway Timeout Runbook")
    assert client.get("/api/v1/knowledge/nope", headers=as_user(users["viewer"])).status_code == 404

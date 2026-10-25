"""Ticket ↔ logs/metrics correlation around the time a customer reported a problem."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.ai.classifier import KeywordClassifier
from app.ai.correlation import Correlation, PatternSignal, ServiceSignals
from app.models import Incident, Ticket, User
from app.scripts.seed import seed
from tests.conftest import as_user

SERVICES = ["auth-service", "notification-service", "order-service", "payment-service", "postgres-primary"]


@pytest.fixture
def demo(db):
    seed(db, now=datetime.now(UTC))
    users = {u.email.split("@")[0]: u for u in db.query(User)}
    tickets = {t.title: t for t in db.scalars(select(Ticket))}
    incidents = {i.title: i for i in db.scalars(select(Incident)).unique()}
    return users, tickets, incidents


def correlation(client, user, number):
    response = client.get(f"/api/v1/tickets/{number}/correlation", headers=as_user(user))
    assert response.status_code == 200
    body = response.json()
    return body, {s["service"]: s for s in body["services"]}


def test_payment_ticket_correlates_with_gateway_timeouts(client, demo):
    users, tickets, incidents = demo
    ticket = tickets["Payment screen times out after login"]
    body, services = correlation(client, users["viewer"], ticket.number)

    start, end = datetime.fromisoformat(body["window_start"]), datetime.fromisoformat(body["window_end"])
    assert start == ticket.created_at - timedelta(minutes=15) and end <= ticket.created_at + timedelta(minutes=15)

    payment = services["payment-service"]
    assert payment["relation"] == "reported" and payment["status"] == "anomalous"
    timeout = next(p for p in payment["patterns"] if p["message"] == "GatewayTimeoutException")
    assert timeout["new"] and timeout["baseline_count"] == 0 and timeout["ref"].startswith("LOG-")
    assert any(m["metric"] == "api_latency_p95_ms" and m["anomalous"] for m in payment["metrics"])

    # The database it depends on is fine; the order-service that depends on it sees the failures.
    assert services["postgres-primary"] == services["postgres-primary"] | {"relation": "dependency", "status": "normal"}
    assert services["order-service"]["relation"] == "dependent"
    assert any(p["message"] == "Payment failed" for p in services["order-service"]["patterns"])
    assert body["suspected_incident"]["number"] == incidents["Payment checkout timeout"].number
    assert body["headline"].startswith("payment-service: ")


def test_quiet_period_shows_normal_services_and_no_incident(client, db, demo):
    users, tickets, _ = demo
    ticket = tickets["Invoice PDF shows wrong VAT rate"]
    ticket.created_at = datetime.now(UTC) - timedelta(hours=5)  # before any injected failure
    db.commit()
    body, services = correlation(client, users["viewer"], ticket.number)
    assert body["suspected_incident"] is None
    assert {s["status"] for s in services.values()} == {"normal"}


def test_ticket_without_service_uses_the_suggested_service(client, db, demo):
    users, tickets, _ = demo
    ticket = tickets["Payment screen times out after login"]
    ticket.service_id = None
    db.commit()
    # The ticket's AI suggestion still names payment-service, so the scope is narrowed to it.
    _, services = correlation(client, users["viewer"], ticket.number)
    assert services["payment-service"]["relation"] == "reported"


def test_vague_ticket_is_routed_to_the_service_with_new_errors():
    correlation = Correlation(
        ticket="TCK-1",
        anchor=datetime.now(UTC),
        window_start=datetime.now(UTC),
        window_end=datetime.now(UTC),
        services=[
            ServiceSignals("order-service", "platform", "anomalous",
                           [PatternSignal("LOG-2", "WARN", "Payment failed", 19, 0, True, "", {})]),
            ServiceSignals("payment-service", "platform", "anomalous",
                           [PatternSignal("LOG-1", "ERROR", "GatewayTimeoutException", 31, 0, True, "", {})]),
            ServiceSignals("postgres-primary", "platform", "normal"),
        ],
        suspected_incident={"number": "INC-421", "title": "Payment checkout timeout", "services": ["payment-service"]},
    )  # fmt: skip
    p = KeywordClassifier(services=SERVICES).classify("Something is weird", "Please call me back.", correlation)
    assert p.service == "payment-service"
    assert p.signals["service_source"] == "most new errors around the ticket's creation"
    assert "Matches active incident INC-421" in p.rationale


def test_link_ticket_to_incident(client, demo):
    users, tickets, incidents = demo
    ticket = tickets["Invoice PDF shows wrong VAT rate"]
    url = f"/api/v1/incidents/{incidents['Payment checkout timeout'].number}/tickets"
    body = {"ticket_number": ticket.number}

    assert client.post(url, json=body, headers=as_user(users["sam"])).status_code == 403
    linked = client.post(url, json=body, headers=as_user(users["maria"]))
    assert linked.status_code == 200 and ticket.number in [t["number"] for t in linked.json()["tickets"]]
    assert client.post(url, json=body, headers=as_user(users["maria"])).status_code == 409


def test_new_ticket_is_matched_to_the_incident_in_its_own_area(client, demo, worker):
    users, _, incidents = demo  # payment and SMTP incidents are both active right now
    ticket = client.post(
        "/api/v1/tickets",
        json={"title": "Customers cannot complete checkout", "description": "The payment page freezes."},
        headers=as_user(users["sam"]),
    ).json()
    worker.drain()
    suggestion = client.get(f"/api/v1/tickets/{ticket['number']}/classification", headers=as_user(users["sam"])).json()
    assert suggestion["service"]["name"] == "payment-service"
    assert suggestion["signals"]["suspected_incident"] == incidents["Payment checkout timeout"].number

"""AI ticket triage with a human in the loop: suggestions are stored, and applied only when an analyst decides."""

import pytest
from sqlalchemy import select

from app.ai.classifier import KeywordClassifier, LLMClassifier
from app.ai.llm import LLMError
from app.models import AuditEvent, Service, Ticket
from tests.conftest import as_user

SERVICES = ["auth-service", "notification-service", "order-service", "payment-service", "postgres-primary"]
URL = "/api/v1/tickets"


@pytest.mark.parametrize(
    ("title", "description", "category", "priority", "service"),
    [
        ("Customers cannot complete checkout", "The payment page freezes.", "PAYMENTS", "P1", "payment-service"),
        ("Dashboard takes 30 seconds to load after login", "Merchants report the dashboard spinner for ~30 s.",
         "PERFORMANCE", "P2", "order-service"),
        ("Users are receiving HTTP 500 when resetting passwords", "12 chats in 20 minutes.",
         "AUTHENTICATION", "P2", "auth-service"),
        ("Webhook retries arrive out of order", "order.updated before order.created.", "NOTIFICATIONS", "P3",
         "notification-service"),
        ("How do I export orders?", "Asked by one merchant.", "ORDERS", "P4", "order-service"),
        ("Something is weird", "Please call me back.", "OTHER", "P3", None),
    ],
)  # fmt: skip
def test_keyword_classifier(title, description, category, priority, service):
    p = KeywordClassifier(services=SERVICES).classify(title, description)
    assert (p.category.value, p.priority.value, p.service) == (category, priority, service)
    assert 0 < p.confidence <= 0.95 and p.rationale


def test_explicit_service_mention_wins():
    p = KeywordClassifier(services=SERVICES).classify("Errors in auth-service", "Payments look fine.")
    assert p.service == "auth-service" and p.signals["service_source"] == "named in the ticket"


@pytest.fixture
def services(db, seeded):
    db.add_all(
        Service(name=n, display_name=n, tier=1, owner_team="x")
        for n in ("order-service", "notification-service", "postgres-primary")
    )
    db.commit()


def create(client, user, **overrides):
    body = {"title": "Customers cannot complete checkout", "description": "Several customers: payment page freezes.",
            "priority": "P3"} | overrides  # fmt: skip
    response = client.post(URL, json=body, headers=as_user(user))
    assert response.status_code == 201
    return response.json()


def test_new_ticket_gets_a_pending_suggestion_that_changes_nothing(client, seeded, services):
    ticket = create(client, seeded["agent"])
    assert ticket["priority"] == "P3" and ticket["category"] is None and ticket["service"] is None

    suggestion = client.get(f"{URL}/{ticket['number']}/classification", headers=as_user(seeded["viewer"])).json()
    assert suggestion["status"] == "PENDING" and suggestion["engine"] == "keywords-v1"
    assert (suggestion["category"], suggestion["priority"], suggestion["service"]["name"]) == (
        "PAYMENTS", "P1", "payment-service",
    )  # fmt: skip
    assert "checkout" in suggestion["rationale"]


def test_accepting_applies_the_suggestion_and_is_recorded(client, db, seeded, services):
    ticket = create(client, seeded["agent"])
    suggestion = client.get(f"{URL}/{ticket['number']}/classification", headers=as_user(seeded["agent"])).json()
    review = f"{URL}/{ticket['number']}/classification/{suggestion['id']}/review"

    assert client.post(review, json={"action": "accept"}, headers=as_user(seeded["viewer"])).status_code == 403
    accepted = client.post(review, json={"action": "accept"}, headers=as_user(seeded["agent"])).json()
    assert accepted["status"] == "ACCEPTED" and accepted["reviewed_by"]["email"] == "agent@opspilot.dev"

    updated = client.get(f"{URL}/{ticket['number']}", headers=as_user(seeded["agent"])).json()
    assert (updated["priority"], updated["category"], updated["service"]["name"]) == (
        "P1",
        "PAYMENTS",
        "payment-service",
    )
    events = [
        e["type"] for e in client.get(f"{URL}/{ticket['number']}/events", headers=as_user(seeded["agent"])).json()
    ]
    assert {"AI_CLASSIFIED", "CATEGORY_CHANGED", "PRIORITY_CHANGED", "AI_SUGGESTION_REVIEWED"} <= set(events)
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "ticket.ai_suggestion_reviewed"))

    # A decision is final: reviewing again is a conflict.
    assert client.post(review, json={"action": "reject"}, headers=as_user(seeded["agent"])).status_code == 409


def test_modify_records_what_the_analyst_corrected(client, seeded, services):
    ticket = create(client, seeded["agent"], title="Typo in password reset e-mail", description="'Pasword' in subject")
    suggestion = client.get(f"{URL}/{ticket['number']}/classification", headers=as_user(seeded["agent"])).json()
    review = f"{URL}/{ticket['number']}/classification/{suggestion['id']}/review"
    headers = as_user(seeded["maria"])

    bad = client.post(review, json={"action": "modify", "service": "nope"}, headers=headers)
    assert bad.status_code == 422
    modified = client.post(
        review, json={"action": "modify", "category": "NOTIFICATIONS", "priority": "P3", "note": "copy fix"},
        headers=headers,
    ).json()  # fmt: skip
    assert modified["status"] == "MODIFIED"
    assert (modified["final_category"], modified["final_priority"]) == ("NOTIFICATIONS", "P3")
    assert modified["category"] == suggestion["category"]  # the prediction itself is never overwritten

    metrics = client.get(f"{URL}/classifications/metrics", headers=as_user(seeded["viewer"])).json()
    assert metrics["by_status"]["MODIFIED"] == 1 and metrics["corrections"]["priority"] == 1
    assert metrics["acceptance_rate"] == 0.0


def test_reject_leaves_the_ticket_alone_and_reclassify_supersedes(client, db, seeded, services):
    ticket = create(client, seeded["agent"])
    first = client.get(f"{URL}/{ticket['number']}/classification", headers=as_user(seeded["agent"])).json()
    second = client.post(f"{URL}/{ticket['number']}/classification", headers=as_user(seeded["agent"])).json()
    assert second["id"] != first["id"] and second["status"] == "PENDING"
    stale = client.post(
        f"{URL}/{ticket['number']}/classification/{first['id']}/review", json={"action": "accept"},
        headers=as_user(seeded["agent"]),
    )  # fmt: skip
    assert stale.status_code == 409  # superseded

    client.post(f"{URL}/{ticket['number']}/classification/{second['id']}/review",
                json={"action": "reject", "note": "duplicate"}, headers=as_user(seeded["agent"]))  # fmt: skip
    row = db.scalar(select(Ticket).where(Ticket.number == ticket["number"]))
    assert row.priority.value == "P3" and row.category is None


class FakeLLM:
    name = "fake-model"

    def __init__(self, answer):
        self.answer = answer

    def complete_json(self, system, prompt, schema, max_tokens=4096):
        if isinstance(self.answer, Exception):
            raise self.answer
        assert schema["properties"]["service"]["enum"][-1] == "unknown"
        return self.answer


def test_llm_classifier_is_constrained_to_the_catalogue():
    services = {name: "" for name in SERVICES}
    fallback = KeywordClassifier(services=SERVICES)
    answer = {"category": "PAYMENTS", "priority": "P1", "service": "payment-service", "confidence": 1.7,
              "rationale": "checkout freezes"}  # fmt: skip
    p = LLMClassifier(FakeLLM(answer), services, fallback).classify("Checkout freezes", "All customers")
    assert (p.engine, p.service, p.confidence) == ("fake-model", "payment-service", 1.0)

    for broken in ({**answer, "category": "BILLING"}, LLMError("model provider unreachable")):
        p = LLMClassifier(FakeLLM(broken), services, fallback).classify("Checkout freezes", "All customers")
        assert p.engine == "keywords-v1" and p.signals["llm_error"]

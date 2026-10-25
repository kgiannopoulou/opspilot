from datetime import UTC, datetime

import pytest

from app.models import Ticket
from app.models.enums import TicketStatus as S
from app.services.ticket_workflow import allowed_transitions, can_transition
from tests.conftest import as_user

API = "/api/v1/tickets"


def _create(client, user, **overrides):
    body = {
        "title": "Payment screen times out after login",
        "description": 'Several customers report that checkout freezes after clicking "Pay".',
        "priority": "P1",
        "service": "payment-service",
        "labels": ["checkout", "payments"],
    } | overrides
    response = client.post(API, json=body, headers=as_user(user))
    assert response.status_code == 201, response.text
    return response.json()


def test_create_ticket_assigns_number_and_records_event(client, seeded):
    ticket = _create(client, seeded["agent"])
    assert ticket["number"] == f"TCK-{datetime.now(UTC).year}-00001"
    assert ticket["status"] == "OPEN"
    assert ticket["service"]["name"] == "payment-service"
    assert ticket["reporter"]["email"] == "agent@opspilot.dev"
    assert ticket["allowed_transitions"] == ["TRIAGED", "INVESTIGATING", "CLOSED"]

    events = client.get(f"{API}/{ticket['number']}/events", headers=as_user(seeded["agent"])).json()
    assert [e["type"] for e in events] == ["CREATED", "AI_CLASSIFIED"]  # triage suggestion, not applied


def test_unknown_service_is_rejected(client, seeded):
    response = client.post(
        API,
        json={"title": "abc", "description": "x", "service": "nope-service"},
        headers=as_user(seeded["agent"]),
    )
    assert response.status_code == 422
    assert "unknown service" in response.json()["detail"]


def test_requests_without_a_valid_token_are_rejected(client, seeded):
    assert client.get(API).status_code == 401
    assert client.get(API, headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_full_lifecycle(client, seeded):
    agent, maria = seeded["agent"], seeded["maria"]
    number = _create(client, agent)["number"]

    def move(status, resolution=None, expect=200):
        body = {"status": status} | ({"resolution": resolution} if resolution else {})
        response = client.post(f"{API}/{number}/transition", json=body, headers=as_user(maria))
        assert response.status_code == expect, response.text
        return response.json()

    assert move("TRIAGED")["triaged_at"] is not None
    move("INVESTIGATING")
    move("RESOLVED", expect=422)  # resolution note required
    resolved = move("RESOLVED", "Gateway timeout raised to 10 s; provider incident acknowledged.")
    assert resolved["resolved_at"] is not None
    assert resolved["resolution"].startswith("Gateway timeout")
    closed = move("CLOSED")
    assert closed["closed_at"] is not None
    assert closed["allowed_transitions"] == []
    move("OPEN", expect=422)

    events = client.get(f"{API}/{number}/events", headers=as_user(agent)).json()
    transitions = [(e["from_value"], e["to_value"]) for e in events if e["type"] == "STATUS_CHANGED"]
    assert transitions == [
        ("OPEN", "TRIAGED"),
        ("TRIAGED", "INVESTIGATING"),
        ("INVESTIGATING", "RESOLVED"),
        ("RESOLVED", "CLOSED"),
    ]
    assert all(e["actor"]["email"] == "maria@opspilot.dev" for e in events if e["type"] == "STATUS_CHANGED")


def test_reopen_clears_resolved_at(client, seeded):
    number = _create(client, seeded["agent"])["number"]
    headers = as_user(seeded["maria"])
    client.post(f"{API}/{number}/transition", json={"status": "INVESTIGATING"}, headers=headers)
    client.post(f"{API}/{number}/transition", json={"status": "RESOLVED", "resolution": "fixed"}, headers=headers)
    reopened = client.post(f"{API}/{number}/transition", json={"status": "INVESTIGATING"}, headers=headers).json()
    assert reopened["status"] == "INVESTIGATING"
    assert reopened["resolved_at"] is None


def test_assignment_and_unassignment(client, seeded):
    agent, maria = seeded["agent"], seeded["maria"]
    number = _create(client, agent)["number"]
    assigned = client.post(f"{API}/{number}/assign", json={"assignee_id": maria.id}, headers=as_user(agent)).json()
    assert assigned["assignee"]["full_name"] == "Maria Engineer"

    bad = client.post(f"{API}/{number}/assign", json={"assignee_id": 9999}, headers=as_user(agent))
    assert bad.status_code == 422

    cleared = client.post(f"{API}/{number}/assign", json={"assignee_id": None}, headers=as_user(agent)).json()
    assert cleared["assignee"] is None

    events = client.get(f"{API}/{number}/events", headers=as_user(agent)).json()
    assert [(e["from_value"], e["to_value"]) for e in events if e["type"] == "ASSIGNED"] == [
        (None, "Maria Engineer"),
        ("Maria Engineer", None),
    ]


def test_comments(client, seeded):
    number = _create(client, seeded["agent"])["number"]
    headers = as_user(seeded["maria"])
    created = client.post(
        f"{API}/{number}/comments",
        json={"body": "Seeing GatewayTimeoutException in logs", "internal": True},
        headers=headers,
    )
    assert created.status_code == 201
    comments = client.get(f"{API}/{number}/comments", headers=headers).json()
    assert len(comments) == 1 and comments[0]["internal"] is True
    assert comments[0]["author"]["full_name"] == "Maria Engineer"


def test_update_records_priority_change(client, seeded):
    number = _create(client, seeded["agent"], priority="P3")["number"]
    headers = as_user(seeded["maria"])
    updated = client.patch(f"{API}/{number}", json={"priority": "P1", "title": "Checkout down"}, headers=headers).json()
    assert updated["priority"] == "P1" and updated["title"] == "Checkout down"
    events = client.get(f"{API}/{number}/events", headers=headers).json()
    assert ("PRIORITY_CHANGED", "P3", "P1") in [(e["type"], e["from_value"], e["to_value"]) for e in events]


def test_list_filters_and_pagination(client, seeded):
    agent = seeded["agent"]
    _create(client, agent, title="Checkout broken", priority="P1")
    _create(client, agent, title="Login slow", priority="P3", service="auth-service")
    _create(client, agent, title="Typo on receipt", priority="P4", service=None)
    headers = as_user(agent)

    everything = client.get(API, headers=headers).json()
    assert everything["total"] == 3
    assert [t["priority"] for t in everything["items"]] == ["P1", "P3", "P4"]

    assert client.get(API, params={"service": "auth-service"}, headers=headers).json()["total"] == 1
    assert client.get(API, params={"priority": ["P1", "P4"]}, headers=headers).json()["total"] == 2
    assert client.get(API, params={"q": "login"}, headers=headers).json()["items"][0]["title"] == "Login slow"
    assert client.get(API, params={"unassigned": True}, headers=headers).json()["total"] == 3

    page2 = client.get(API, params={"page": 2, "page_size": 2}, headers=headers).json()
    assert len(page2["items"]) == 1 and page2["total"] == 3


def test_unknown_ticket_is_404(client, seeded):
    assert client.get(f"{API}/TCK-2026-99999", headers=as_user(seeded["agent"])).status_code == 404


def test_invalid_label_rejected(client, seeded):
    response = client.post(
        API, json={"title": "abc", "description": "x", "labels": ["Has Spaces"]}, headers=as_user(seeded["agent"])
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("current", "target", "ok"),
    [
        (S.OPEN, S.TRIAGED, True),
        (S.OPEN, S.RESOLVED, False),
        (S.RESOLVED, S.INVESTIGATING, True),
        (S.CLOSED, S.OPEN, False),
    ],
)
def test_workflow_rules(current, target, ok):
    assert can_transition(current, target) is ok


def test_allowed_transitions_are_ordered():
    assert allowed_transitions(S.TRIAGED) == [S.OPEN, S.INVESTIGATING, S.RESOLVED]


def test_demo_seed_loads(db):
    from app.scripts.seed import seed

    seed(db)
    statuses = {t.status for t in db.query(Ticket).all()}
    assert {S.OPEN, S.TRIAGED, S.INVESTIGATING, S.RESOLVED, S.CLOSED} <= statuses

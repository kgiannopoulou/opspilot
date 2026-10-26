from datetime import UTC, datetime, timedelta

from tests.conftest import as_user


def test_engineer_declares_an_incident_and_links_tickets(client, seeded):
    ticket = client.post("/api/v1/tickets", headers=as_user(seeded["agent"]),
                         json={"title": "Checkout broken", "description": "Pay button errors"}).json()  # fmt: skip
    started = (datetime.now(UTC) - timedelta(minutes=12)).isoformat()
    body = {"title": "Checkout failures", "severity": "SEV1", "primary_service": "payment-service",
            "started_at": started, "summary": "Customers cannot pay", "ticket_numbers": [ticket["number"]]}  # fmt: skip

    assert client.post("/api/v1/incidents", headers=as_user(seeded["agent"]), json=body).status_code == 403
    response = client.post("/api/v1/incidents", headers=as_user(seeded["maria"]), json=body)
    assert response.status_code == 201
    incident = response.json()
    assert incident["number"].startswith("INC-") and incident["status"] == "INVESTIGATING"
    assert incident["commander"]["email"] == "maria@opspilot.dev"
    assert [t["number"] for t in incident["tickets"]] == [ticket["number"]]

    activity = client.get(f"/api/v1/incidents/{incident['number']}/activity", headers=as_user(seeded["viewer"])).json()
    descriptions = [a["description"] for a in activity]
    assert f"Incident {incident['number']} declared by Maria Engineer (SEV1)" in descriptions
    assert f"Maria Engineer linked {ticket['number']} to {incident['number']}" in descriptions


def test_declare_validation(client, seeded):
    maria = as_user(seeded["maria"])
    base = {"title": "Something", "severity": "SEV2", "primary_service": "payment-service"}
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert client.post("/api/v1/incidents", headers=maria, json={**base, "started_at": future}).status_code == 422
    assert client.post("/api/v1/incidents", headers=maria, json={**base, "primary_service": "nope"}).status_code == 422
    assert client.post("/api/v1/incidents", headers=maria, json={**base, "severity": "SEV9"}).status_code == 422
    unknown_ticket = client.post("/api/v1/incidents", headers=maria, json={**base, "ticket_numbers": ["TCK-0"]})
    assert unknown_ticket.status_code == 422
    # Nothing half-written: the failed declarations left no incident behind.
    assert client.get("/api/v1/incidents", headers=maria).json()["total"] == 0

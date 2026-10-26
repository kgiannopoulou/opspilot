"""The audit trail: complete (every important action), readable (activity timeline) and tamper-evident (hash chain)."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.models import AuditEvent
from app.schemas.ticket import TicketCreate, TicketUpdate
from app.services import audit
from app.services.audit import AuditService
from app.services.tickets import TicketService
from tests.conftest import as_user


def _ticket(db, seeded, title="Payment screen times out after login"):
    return TicketService(db).create(TicketCreate(title=title, description="Checkout freezes."), seeded["agent"])


def test_rows_are_hash_chained_by_the_database(db, seeded):
    _ticket(db, seeded)
    _ticket(db, seeded, "Second ticket")
    rows = list(db.scalars(select(AuditEvent).order_by(AuditEvent.chain_seq)).unique())
    assert [r.chain_seq for r in rows] == list(range(1, len(rows) + 1))
    assert rows[0].prev_hash == audit.GENESIS_HASH
    assert all(b.prev_hash == a.hash for a, b in zip(rows, rows[1:], strict=False))
    assert all(len(r.hash) == 64 for r in rows)

    result = AuditService(db).verify()
    assert result.valid and result.rows == len(rows) and result.head_hash == rows[-1].hash


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET action = 'ticket.nothing_to_see' WHERE chain_seq = 1",
        "DELETE FROM audit_events WHERE chain_seq = 1",
        "TRUNCATE audit_events",
    ],
)
def test_audit_trail_is_append_only(db, seeded, statement):
    _ticket(db, seeded)
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text(statement))
    db.rollback()
    assert AuditService(db).verify().valid


def test_users_with_history_cannot_be_erased(db, seeded):
    _ticket(db, seeded)
    with pytest.raises(DBAPIError):
        db.execute(text("DELETE FROM users WHERE id = :id"), {"id": seeded["agent"].id})
    db.rollback()


def test_tampering_is_detected(db, seeded):
    """Someone with database access edits a row, or deletes one, without fixing the chain: verify() points at it."""
    for n in range(4):
        _ticket(db, seeded, f"Ticket {n}")
    with audit.maintenance(db):
        db.execute(text('UPDATE audit_events SET changes = \'{"to_value": "CLOSED"}\' WHERE chain_seq = 2'))
        db.execute(text("DELETE FROM audit_events WHERE chain_seq = 3"))
        db.commit()

    result = AuditService(db).verify()
    assert not result.valid
    assert [(b.chain_seq, b.reason) for b in result.breaks] == [(2, "modified"), (4, "gap")]


def test_rechain_requires_maintenance_mode(db, seeded):
    _ticket(db, seeded)
    with pytest.raises(DBAPIError, match="maintenance"):
        audit.rechain(db)
    db.rollback()


def test_service_change_is_recorded_with_old_and_new_value(db, seeded):
    ticket = _ticket(db, seeded)
    TicketService(db).update(ticket.number, TicketUpdate(service="payment-service"), seeded["maria"])
    items = AuditService(db).entity_activity("ticket", ticket.number)
    assert [audit.describe(e) for e in items] == [
        f"Ticket {ticket.number} created by Sam Agent",
        f"Maria Engineer changed service on {ticket.number} → payment-service",
    ]


def test_incident_activity_tells_the_story(client, db, demo, worker):
    """The seeded payment incident: customer tickets, triage, assignment, investigation, acceptance, resolution."""
    users, incidents = demo
    number = incidents["Payment checkout timeout"]
    maria = as_user(users["maria"])
    inv = client.get(f"/api/v1/incidents/{number}", headers=maria).json()["latest_investigation"]

    accepted = client.post(f"/api/v1/incidents/{number}/status", headers=maria,
                           json={"status": "IDENTIFIED", "root_cause": inv["root_cause"],
                                 "accepted_investigation_id": inv["id"]})  # fmt: skip
    assert accepted.status_code == 200
    client.post(f"/api/v1/incidents/{number}/status", headers=maria, json={"status": "RESOLVED"})

    activity = client.get(f"/api/v1/incidents/{number}/activity", headers=as_user(users["viewer"])).json()
    text_ = [a["description"] for a in activity]
    first_ticket = activity[0]["entity_id"]
    assert text_[0] == f"Ticket {first_ticket} created by Sam Rivera"
    assert f"Sam Rivera assigned {first_ticket} to Maria Papadopoulou" in text_
    assert any(t.startswith("AI predicted PAYMENTS") for t in text_)
    assert any(t.startswith(f"Incident {number} declared by Maria Papadopoulou") for t in text_)
    assert "AI investigation started" in text_
    assert any(t.startswith("AI investigation generated: payment_gateway_timeout") for t in text_)
    assert text_[-3:] == [
        f"Maria Papadopoulou accepted the AI root cause from investigation #{inv['id']}",
        "Maria Papadopoulou moved the incident INVESTIGATING → IDENTIFIED",
        "Incident resolved by Maria Papadopoulou",
    ]
    times = [a["occurred_at"] for a in activity]
    assert times == sorted(times)
    assert "ip_address" not in activity[0] and "hash" not in activity[0]  # analysts get the story, not the forensics

    # The demo seed backdates history and re-chains, so a fresh environment verifies cleanly.
    assert AuditService(db).verify().valid


def test_accepting_a_foreign_investigation_is_refused(client, demo):
    users, incidents = demo
    pool = client.get(f"/api/v1/incidents/{incidents['Merchant dashboard extremely slow']}",
                      headers=as_user(users["maria"])).json()  # fmt: skip
    other = pool["latest_investigation"]["id"]
    body = {"status": "IDENTIFIED", "root_cause": "x", "accepted_investigation_id": other}
    number = incidents["Payment checkout timeout"]
    response = client.post(f"/api/v1/incidents/{number}/status", headers=as_user(users["maria"]), json=body)
    assert response.status_code == 422


def test_worker_triage_is_attributed_to_the_system(client, db, seeded, worker):
    body = {"title": "Customers cannot pay", "description": "Payment page freezes"}
    created = client.post("/api/v1/tickets", headers=as_user(seeded["agent"]), json=body).json()
    worker.drain()
    items = client.get(f"/api/v1/tickets/{created['number']}/activity", headers=as_user(seeded["viewer"])).json()
    assert [i["description"].split(" for ")[0] for i in items[:2]] == [
        f"Ticket {created['number']} created by Sam Agent", "AI classification started",
    ]  # fmt: skip
    assert items[2]["actor"] is None and items[2]["description"].startswith("AI predicted")


def test_audit_api_is_admin_only_and_filterable(client, db, seeded):
    ticket = _ticket(db, seeded)
    assert client.get("/api/v1/audit", headers=as_user(seeded["maria"])).status_code == 403
    assert client.get("/api/v1/audit/verify", headers=as_user(seeded["agent"])).status_code == 403

    admin = as_user(seeded["admin"])
    page = client.get("/api/v1/audit", params={"entity_type": "ticket", "action": "ticket."}, headers=admin).json()
    assert page["total"] == 1 and page["items"][0]["entity_id"] == ticket.number
    assert page["items"][0]["hash"] and page["items"][0]["chain_seq"] == 1

    verified = client.get("/api/v1/audit/verify", headers=admin).json()
    assert verified["valid"] and verified["rows"] == 1 and verified["breaks"] == []

    export = client.get("/api/v1/audit/export.csv", headers=admin)
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
    lines = export.text.strip().splitlines()
    assert lines[0].startswith("chain_seq,occurred_at,actor,action")
    # The export includes the verification itself: auditing the auditors.
    assert [line.split(",")[3] for line in lines[1:]] == ["ticket.created", "audit.verified", "audit.exported"]

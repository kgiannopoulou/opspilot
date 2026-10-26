"""AI feedback: 👍 / 👎 on investigations, stored per person, audited, and rolled up into the AI quality report."""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import AIFeedback, AuditEvent
from tests.conftest import as_user


def _latest(client, user, number):
    return client.get(f"/api/v1/incidents/{number}", headers=as_user(user)).json()["latest_investigation"]


def test_rate_change_and_withdraw(client, db, demo):
    users, incidents = demo
    number = incidents["Payment checkout timeout"]
    inv = _latest(client, users["alex"], number)
    url = f"/api/v1/incidents/{number}/investigations/{inv['id']}/feedback"
    alex = as_user(users["alex"])

    # The seed already has two 👍 on this investigation (Maria, Sam).
    before = client.get(url, headers=alex).json()
    assert before["mine"] is None and before["useful"] == 2 and before["incorrect"] == 0

    useful = client.put(url, headers=alex, json={"rating": 1, "comment": "Spot on"})
    assert (
        useful.status_code == 200
        and useful.json()["rating"] == 1
        and useful.json()["user"]["email"] == "alex@opspilot.dev"
    )

    # Changing your mind replaces the verdict (one row per person); "incorrect" needs a reason.
    assert client.put(url, headers=alex, json={"rating": -1}).status_code == 422
    changed = client.put(url, headers=alex, json={"rating": -1, "reason": "MISSING_EVIDENCE", "comment": "  "}).json()
    assert (
        changed["id"] == useful.json()["id"] and changed["reason"] == "MISSING_EVIDENCE" and changed["comment"] is None
    )
    summary = client.get(url, headers=alex).json()
    assert summary["mine"]["rating"] == -1 and summary["useful"] == 2 and summary["incorrect"] == 1
    assert summary["reasons"] == {"MISSING_EVIDENCE": 1}

    assert client.delete(url, headers=alex).status_code == 204
    assert client.get(url, headers=alex).json()["mine"] is None

    mine = (
        AuditEvent.action.startswith("ai.feedback"),
        AuditEvent.actor_id == users["alex"].id,
        AuditEvent.entity_id == number,
    )
    actions = list(db.scalars(select(AuditEvent.action).where(*mine).order_by(AuditEvent.chain_seq)))
    assert actions == ["ai.feedback_submitted", "ai.feedback_submitted", "ai.feedback_withdrawn"]


def test_only_people_who_work_incidents_can_rate(client, demo):
    users, incidents = demo
    number = incidents["Payment checkout timeout"]
    inv = _latest(client, users["viewer"], number)
    url = f"/api/v1/incidents/{number}/investigations/{inv['id']}/feedback"
    assert client.put(url, json={"rating": 1}).status_code == 401
    assert client.put(url, headers=as_user(users["viewer"]), json={"rating": 1}).status_code == 403
    assert client.get(url, headers=as_user(users["viewer"])).status_code == 200  # but can see the counts
    assert client.put(url, headers=as_user(users["sam"]), json={"rating": -1, "reason": "OTHER"}).status_code == 200


def test_running_investigations_cannot_be_rated(client, demo):
    users, incidents = demo
    number = incidents["HTTP 500 on password reset"]
    queued = client.post(f"/api/v1/incidents/{number}/investigations", headers=as_user(users["maria"])).json()
    response = client.put(f"/api/v1/incidents/{number}/investigations/{queued['id']}/feedback",
                          headers=as_user(users["maria"]), json={"rating": 1})  # fmt: skip
    assert response.status_code == 409


def test_investigation_must_belong_to_the_incident(client, demo):
    users, incidents = demo
    other = _latest(client, users["maria"], incidents["Merchant dashboard extremely slow"])
    response = client.put(f"/api/v1/incidents/{incidents['Payment checkout timeout']}/investigations/{other['id']}"
                          "/feedback", headers=as_user(users["maria"]), json={"rating": 1})  # fmt: skip
    assert response.status_code == 404


def test_database_enforces_the_rules(db, demo):
    users, _ = demo
    row = db.scalar(select(AIFeedback).limit(1))
    with pytest.raises(IntegrityError):  # second verdict from the same person
        db.add(AIFeedback(investigation_id=row.investigation_id, user_id=row.user_id, rating=1))
        db.flush()
    db.rollback()
    with pytest.raises(IntegrityError, match="negative_needs_reason"):
        db.execute(text("UPDATE ai_feedback SET rating = -1, reason = NULL WHERE id = :id"), {"id": row.id})
    db.rollback()
    with pytest.raises(IntegrityError, match="rating_values"):
        db.execute(text("UPDATE ai_feedback SET rating = 5, reason = 'OTHER' WHERE id = :id"), {"id": row.id})
    db.rollback()


def test_quality_report(client, db, demo):
    users, _ = demo
    report = client.get("/api/v1/ai/quality", headers=as_user(users["viewer"])).json()
    inv = report["investigations"]
    # From the seed: payment 👍👍, pool 👍👎 (unhelpful steps).
    assert db.scalar(select(func.count()).select_from(AIFeedback)) == 4
    assert (inv["votes"], inv["useful"], inv["incorrect"], inv["rated"]) == (4, 3, 1, 2)
    assert inv["useful_rate"] == 0.75 and inv["reasons"] == {"UNHELPFUL_STEPS": 1}
    assert inv["by_hypothesis"]["db_connection_pool_exhaustion"] == {"useful": 1, "incorrect": 1, "useful_rate": 0.5}
    assert inv["by_engine"]["rules-v1+rag/extractive-v1"]["useful_rate"] == 0.75
    assert inv["completed"] == 2 and inv["grounding_passed"] == 2 and inv["llm_drafts"] == 0
    assert report["recent_negative"][0]["reason"] == "UNHELPFUL_STEPS"
    assert report["recent_negative"][0]["user"] == "Nikos Georgiou"
    assert report["triage"]["by_status"]["MODIFIED"] == 1  # the triage review loop sits next to it

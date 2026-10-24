from datetime import UTC, datetime

import pytest

from app.models import User
from app.scripts.seed import seed
from tests.conftest import as_user


@pytest.fixture
def demo(db):
    seed(db, now=datetime.now(UTC))
    return {u.email.split("@")[0]: u for u in db.query(User)}


def test_dashboard_summary(client, demo):
    response = client.get("/api/v1/dashboard/summary", headers=as_user(demo["viewer"]))
    assert response.status_code == 200
    data = response.json()

    assert data["active_incidents"] == {"SEV1": 1, "SEV2": 2, "SEV3": 0, "SEV4": 0}
    assert data["resolved_last_30d"] > 0 and data["mttr_minutes"] > 0
    assert len(data["incident_trend"]) == len(data["ticket_volume"]) == 30
    assert data["tickets"]["open_p1"] == 2

    # Live incidents come first by severity: the SEV1 checkout outage leads.
    assert data["active"][0]["title"] == "Payment checkout timeout"
    assert data["active"][0]["ticket_count"] == 2

    health = {s["name"]: s for s in data["services"]}
    assert health["payment-service"]["status"] == "critical"
    assert health["payment-service"]["latency_p95_ms"] > 4 * health["payment-service"]["latency_baseline_ms"]
    assert health["postgres-primary"]["status"] == "healthy"
    assert len(health["payment-service"]["error_rate_series"]) >= 70


def test_dashboard_requires_authentication(client, demo):
    assert client.get("/api/v1/dashboard/summary").status_code == 401


def test_incident_list_filters(client, demo):
    headers = as_user(demo["sam"])
    active = client.get("/api/v1/incidents", params={"state": "active"}, headers=headers).json()
    assert active["total"] == 3
    assert {i["status"] for i in active["items"]} == {"INVESTIGATING", "MONITORING"}

    everything = client.get("/api/v1/incidents", headers=headers).json()
    assert everything["items"][0]["status"] != "RESOLVED"  # active first
    sev1 = client.get("/api/v1/incidents", params={"severity": "SEV1"}, headers=headers).json()
    assert all(i["severity"] == "SEV1" for i in sev1["items"])

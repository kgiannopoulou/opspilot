"""AI-proposed investigation queries: validation, sandboxed execution and the audit trail."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.ai.query_planner import QueryPlanner
from app.ai.sql_guard import Verdict, validate
from app.models import AuditEvent, Incident, QueryRun, User, shop_payments
from app.scripts.seed import seed
from app.services import queries as query_service
from app.services.investigations import InvestigationService
from tests.conftest import as_user

ALLOWED = [
    "SELECT status, COUNT(*) FROM payments GROUP BY status",
    "select /* comment */ * from SHOPSTREAM.payments",
    "WITH t AS (SELECT * FROM payments) SELECT count(*) FROM t",
    "SELECT 1 FROM payments UNION SELECT 2 FROM orders",
    "SELECT pid, now() - query_start AS runtime FROM pg_stat_activity",
]
BLOCKED = {
    "DELETE FROM payments": "DELETE is not allowed",
    "SELECT * FROM payments; DROP TABLE payments": "exactly one statement allowed, got 2",
    "SELECT email, password_hash FROM users": "table users is not on the allowlist",
    "SELECT * FROM public.tickets": "table public.tickets is not on the allowlist",
    "SELECT pg_sleep(10)": "function pg_sleep() is not on the allowlist",
    "SELECT pg_read_file('/etc/passwd')": "function pg_read_file() is not on the allowlist",
    "SELECT set_config('role', 'postgres', false)": "function set_config() is not on the allowlist",
    "SELECT * INTO copy_of_payments FROM payments": "INTO is not allowed",
    "SELECT * FROM payments FOR UPDATE": "LOCK is not allowed",
    "COPY payments TO '/tmp/x'": "COPY is not allowed",
    "SELECT * FROM generate_series(1, 1000000)": "table-valued functions are not allowed",
}


@pytest.mark.parametrize("sql", ALLOWED)
def test_validator_accepts_read_only_selects_and_enforces_a_limit(sql):
    verdict = validate(sql)
    assert verdict.ok, verdict.violations
    assert verdict.safe_sql.rstrip().endswith("LIMIT 200")


@pytest.mark.parametrize(("sql", "violation"), BLOCKED.items())
def test_validator_blocks(sql, violation):
    verdict = validate(sql)
    assert not verdict.ok and violation in verdict.violations


def test_existing_limit_is_lowered_not_raised():
    assert validate("SELECT * FROM payments LIMIT 10").safe_sql.endswith("LIMIT 10")
    assert validate("SELECT * FROM payments LIMIT 100000").safe_sql.endswith("LIMIT 200")


@pytest.fixture
def demo(db):
    seed(db, now=datetime.now(UTC))
    users = {u.email.split("@")[0]: u for u in db.query(User)}
    incident = db.scalar(select(Incident).where(Incident.title == "Payment checkout timeout"))
    return users, incident


def queries_of(client, user, incident):
    inv = client.get(f"/api/v1/incidents/{incident.number}", headers=as_user(user)).json()["latest_investigation"]
    return inv, inv["queries"]


def run_url(incident, inv, query):
    return f"/api/v1/incidents/{incident.number}/investigations/{inv['id']}/queries/{query['id']}/run"


def test_investigation_proposes_validated_queries(client, demo):
    users, incident = demo
    inv, queries = queries_of(client, users["viewer"], incident)
    assert [q["title"] for q in queries][:2] == ["Payment outcomes during the incident", "Timeouts per minute"]
    assert {q["risk"] for q in queries} == {"READ_ONLY"}
    first = queries[0]
    assert first["source"] == "template:payment_gateway_timeout" and first["tables"] == ["payments"]
    assert inv["window_start"][:16] in first["sql"]  # the window is in the query, so re-runs are reproducible


def test_engineer_runs_a_query_in_the_sandbox(client, db, demo):
    users, incident = demo
    inv, queries = queries_of(client, users["viewer"], incident)
    url = run_url(incident, inv, queries[0])

    assert client.post(url, json={}, headers=as_user(users["sam"])).status_code == 403  # support agents cannot
    run = client.post(url, json={}, headers=as_user(users["maria"])).json()
    assert run["status"] == "SUCCEEDED" and run["columns"] == ["status", "payments", "avg_gateway_ms"]
    outcomes = {row[0]: row[1] for row in run["rows"]}
    assert outcomes["TIMEOUT"] > 20 and outcomes["CAPTURED"] > 0
    assert run["sql"].rstrip().endswith("LIMIT 200") and not run["edited"]

    _, queries = queries_of(client, users["viewer"], incident)
    assert queries[0]["last_run"]["id"] == run["id"]
    audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "query.executed"))
    assert audit.entity_id == incident.number and audit.changes["rows"] == run["row_count"]


def test_edited_sql_is_validated_again(client, db, demo):
    users, incident = demo
    inv, queries = queries_of(client, users["viewer"], incident)
    url = run_url(incident, inv, queries[0])
    headers = as_user(users["maria"])

    for sql in ("DELETE FROM payments", "SELECT email, password_hash FROM users"):
        run = client.post(url, json={"sql": sql}, headers=headers).json()
        assert run["status"] == "REJECTED" and run["edited"] and run["rows"] == []
    edited = client.post(url, json={"sql": "SELECT COUNT(*) AS n FROM payments WHERE status = 'TIMEOUT'"},
                         headers=headers).json()  # fmt: skip
    assert edited["status"] == "SUCCEEDED" and edited["edited"] and edited["rows"][0][0] > 20
    assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "query.rejected")) == 2


@pytest.fixture
def validator_bypassed(monkeypatch):
    """Simulate a validator bug: everything passes. The database layers must still hold."""
    monkeypatch.setattr(query_service, "validate", lambda sql: Verdict(ok=True, sql=sql, safe_sql=sql))


@pytest.mark.parametrize(
    ("sql", "error"),
    [
        ("SELECT email, password_hash FROM public.users", "permission denied for table users"),
        ("DELETE FROM shopstream.payments", "read-only transaction"),
        ("UPDATE shopstream.payments SET status = 'CAPTURED'", "read-only transaction"),
    ],
)
def test_sandbox_holds_even_if_the_validator_fails(client, db, demo, validator_bypassed, sql, error):
    users, incident = demo
    before = db.scalar(select(func.count()).select_from(shop_payments).where(shop_payments.c.status == "TIMEOUT"))
    inv, queries = queries_of(client, users["viewer"], incident)
    run = client.post(run_url(incident, inv, queries[0]), json={"sql": sql}, headers=as_user(users["maria"])).json()
    assert run["status"] == "FAILED" and error in run["error"]
    after = db.scalar(select(func.count()).select_from(shop_payments).where(shop_payments.c.status == "TIMEOUT"))
    assert after == before > 0


def test_slow_queries_are_cancelled(client, demo, validator_bypassed, monkeypatch):
    monkeypatch.setattr(query_service, "STATEMENT_TIMEOUT_MS", 200)
    users, incident = demo
    inv, queries = queries_of(client, users["viewer"], incident)
    run = client.post(run_url(incident, inv, queries[0]), json={"sql": "SELECT pg_sleep(2)"},
                      headers=as_user(users["maria"])).json()  # fmt: skip
    assert run["status"] == "FAILED" and "statement timeout" in run["error"]


def test_validate_endpoint(client, demo):
    users, _ = demo
    ok = client.post("/api/v1/sql/validate", json={"sql": "SELECT * FROM orders"}, headers=as_user(users["maria"]))
    assert ok.json()["risk"] == "READ_ONLY" and ok.json()["tables"] == ["orders"]
    bad = client.post("/api/v1/sql/validate", json={"sql": "DROP TABLE orders"}, headers=as_user(users["maria"]))
    assert bad.json()["risk"] == "BLOCKED"
    assert client.post("/api/v1/sql/validate", json={"sql": "x"}, headers=as_user(users["viewer"])).status_code == 403


class FakeLLM:
    name = "fake-model"

    def complete_json(self, system, prompt, schema, max_tokens=4096):
        assert "payments(" in prompt and "ALREADY PROPOSED" in prompt
        return {
            "queries": [
                {"title": "Declines vs timeouts", "purpose": "Rules out issuer declines.",
                 "sql": "SELECT status, COUNT(*) FROM payments GROUP BY status"},
                {"title": "Clean up", "purpose": "Remove stuck rows.",
                 "sql": "DELETE FROM payments WHERE status = 'PENDING'"},
            ]
        }  # fmt: skip


def test_llm_proposals_are_stored_with_their_verdict(db, demo):
    users, incident = demo
    run = InvestigationService(db, planner=QueryPlanner(FakeLLM())).run(incident, users["maria"])
    llm_queries = [q for q in run.queries if q.source == "fake-model"]
    assert [(q.title, q.risk.value) for q in llm_queries] == [
        ("Declines vs timeouts", "READ_ONLY"),
        ("Clean up", "BLOCKED"),
    ]
    assert llm_queries[1].violations and llm_queries[1].safe_sql is None
    assert db.query(QueryRun).count() == 0  # proposing never executes anything

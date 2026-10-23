from datetime import UTC, datetime, timedelta

import pytest

from simulator.engine import ScheduledIncident, simulate
from simulator.incidents.scenarios import SCENARIOS
from simulator.topology import SERVICES, dependents_of

START = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
END = START + timedelta(hours=3)


def _incident(key: str = "payment_gateway_timeout") -> ScheduledIncident:
    return ScheduledIncident(key, START + timedelta(hours=2), timedelta(minutes=30))


def test_same_seed_is_reproducible() -> None:
    a = simulate(START, END, [_incident()], seed=7)
    b = simulate(START, END, [_incident()], seed=7)
    assert [log.format() for log in a.logs] == [log.format() for log in b.logs]
    assert a.metrics == b.metrics


def test_healthy_run_has_no_errors_and_covers_every_service() -> None:
    result = simulate(START, END, seed=1)
    assert not [log for log in result.logs if log.level == "ERROR"]
    assert {m.service for m in result.metrics} == set(SERVICES)
    assert {log.service for log in result.logs} == set(SERVICES)


def test_logs_are_sorted_and_inside_window() -> None:
    result = simulate(START, END, [_incident()])
    stamps = [log.timestamp for log in result.logs]
    assert stamps == sorted(stamps)
    assert stamps[0] >= START and stamps[-1] < END


def test_gateway_timeout_produces_signature_and_latency_spike() -> None:
    incident = _incident()
    result = simulate(START, END, [incident])
    errors = [log for log in result.logs if log.message == "GatewayTimeoutException"]
    assert 20 <= len(errors) <= 45
    assert all(incident.start <= e.timestamp < incident.end + timedelta(seconds=10) for e in errors)

    def latency(window_start: datetime, window_end: datetime) -> float:
        values = [
            m.value
            for m in result.metrics
            if m.service == "payment-service"
            and m.name == "api_latency_p95_ms"
            and window_start <= m.timestamp < window_end
        ]
        return sum(values) / len(values)

    baseline = latency(START, incident.start)
    during = latency(incident.start + timedelta(minutes=5), incident.end)
    assert during / baseline > 4

    db_latency = [
        m.value
        for m in result.metrics
        if m.service == "postgres-primary" and m.name == "query_latency_p95_ms" and m.timestamp >= incident.start
    ]
    assert max(db_latency) < 25  # the database is healthy in this scenario


@pytest.mark.parametrize("key", sorted(SCENARIOS))
def test_every_scenario_leaves_its_signature(key: str) -> None:
    scenario = SCENARIOS[key]
    result = simulate(START, END, [_incident(key)])
    assert any(scenario.signature in log.message for log in result.logs)
    assert scenario.reports


def test_log_line_format() -> None:
    result = simulate(START, END, [_incident()])
    line = next(log for log in result.logs if log.message == "GatewayTimeoutException").format()
    assert " ERROR payment-service  GatewayTimeoutException provider=stripe timeout=5000ms" in line


def test_unknown_scenario_rejected() -> None:
    with pytest.raises(ValueError):
        simulate(START, END, [ScheduledIncident("nope", START, timedelta(minutes=5))])


def test_dependents() -> None:
    assert dependents_of("payment-service") == ["order-service"]
    assert "auth-service" in dependents_of("postgres-primary")

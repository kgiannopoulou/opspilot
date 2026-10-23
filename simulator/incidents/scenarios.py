"""Injectable failure scenarios. Each one produces the logs and metric distortions a real outage would leave behind."""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from simulator.log_generator.generator import order_id, trace_id
from simulator.metric_generator.generator import MetricOverride
from simulator.records import LogRecord


@dataclass(frozen=True)
class CustomerReport:
    title: str
    description: str
    priority: str  # P1..P4


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    root_cause_service: str
    symptom_service: str  # where customers feel it
    severity: str  # SEV1..SEV4
    signature: str  # the error signature an engineer would grep for
    summary: str
    reports: tuple[CustomerReport, ...]
    logs: Callable[[random.Random, datetime, datetime], Iterator[LogRecord]]
    # metric -> (service, metric, peak value); values ramp up over the first minutes of the incident
    metric_peaks: tuple[tuple[str, str, float], ...]


def _per_minute(rng: random.Random, start: datetime, end: datetime, rate: float) -> Iterator[datetime]:
    minute = start
    while minute < end:
        count = int(rate) + (1 if rng.random() < rate - int(rate) else 0)
        for _ in range(count):
            yield minute + timedelta(seconds=rng.uniform(0, 59))
        minute += timedelta(minutes=1)


# --- payment gateway timeout -------------------------------------------------------------------------------


def _gateway_timeout_logs(rng: random.Random, start: datetime, end: datetime) -> Iterator[LogRecord]:
    for ts in _per_minute(rng, start, end, rate=1.05):
        oid, tid = order_id(rng), trace_id(rng)
        yield LogRecord(ts, "order-service", "INFO", "Checkout started", {"order_id": oid}, tid)
        yield LogRecord(
            ts + timedelta(milliseconds=40),
            "payment-service",
            "INFO",
            "Payment request initiated",
            {"order_id": oid},
            tid,
        )
        yield LogRecord(
            ts + timedelta(milliseconds=60),
            "payment-service",
            "INFO",
            "Calling payment gateway",
            {"provider": "stripe"},
            tid,
        )
        yield LogRecord(
            ts + timedelta(milliseconds=5060),
            "payment-service",
            "ERROR",
            "GatewayTimeoutException",
            {"provider": "stripe", "timeout": "5000ms", "order_id": oid},
            tid,
        )
        yield LogRecord(
            ts + timedelta(milliseconds=5100),
            "order-service",
            "WARN",
            "Payment failed",
            {"order_id": oid, "retry": rng.choice([1, 1, 2])},
            tid,
        )


PAYMENT_GATEWAY_TIMEOUT = Scenario(
    key="payment_gateway_timeout",
    title="Payment checkout timeout",
    root_cause_service="payment-service",
    symptom_service="payment-service",
    severity="SEV1",
    signature="GatewayTimeoutException",
    summary="The external payment gateway responds slower than the 5 s client timeout; checkouts fail.",
    reports=(
        CustomerReport(
            "Payment screen times out after login",
            'Several customers report that checkout freezes after clicking "Pay".',
            "P1",
        ),
        CustomerReport(
            'Payment fails after pressing "Confirm Order"',
            "Merchant m_412 says 4 of their last 10 orders failed at payment with a generic error.",
            "P1",
        ),
    ),
    logs=_gateway_timeout_logs,
    metric_peaks=(
        ("payment-service", "api_latency_p95_ms", 5321),
        ("payment-service", "error_rate", 0.17),
        ("order-service", "api_latency_p95_ms", 5640),
        ("order-service", "error_rate", 0.08),
    ),
)


# --- database connection pool exhaustion ---------------------------------------------------------------------


def _pool_exhaustion_logs(rng: random.Random, start: datetime, end: datetime) -> Iterator[LogRecord]:
    yield LogRecord(
        start - timedelta(minutes=2),
        "order-service",
        "INFO",
        "Scheduled job started",
        {"job": "merchant_revenue_report", "merchants": 2143},
    )
    for ts in _per_minute(rng, start, end, rate=0.8):
        yield LogRecord(
            ts,
            "postgres-primary",
            "WARN",
            "slow statement",
            {
                "duration_ms": int(rng.gauss(27800, 1500)),
                "statement": "SELECT ... FROM order_items JOIN orders ... GROUP BY merchant_id",
                "plan": "Seq Scan on order_items",
            },
        )
    for ts in _per_minute(rng, start + timedelta(minutes=2), end, rate=1.4):
        merchant = f"m_{rng.randint(100, 999)}"
        if rng.random() < 0.55:
            yield LogRecord(
                ts,
                "order-service",
                "ERROR",
                "TimeoutError: QueuePool limit of size 20 overflow 10 reached",
                {"timeout": "30.00s", "endpoint": "/merchant/dashboard"},
            )
        else:
            yield LogRecord(
                ts,
                "order-service",
                "WARN",
                "GET /merchant/dashboard 200",
                {"merchant": merchant, "duration_ms": int(rng.gauss(29500, 2000))},
            )


DB_POOL_EXHAUSTION = Scenario(
    key="db_pool_exhaustion",
    title="Merchant dashboard extremely slow",
    root_cause_service="postgres-primary",
    symptom_service="order-service",
    severity="SEV2",
    signature="QueuePool limit",
    summary="A reporting job runs unindexed queries that hold database connections; the pool is exhausted.",
    reports=(
        CustomerReport(
            "Dashboard takes 30 seconds to load after login",
            "Merchants report the dashboard spinner for ~30 s, sometimes ending in an error page.",
            "P2",
        ),
    ),
    logs=_pool_exhaustion_logs,
    metric_peaks=(
        ("postgres-primary", "database_connections", 99),
        ("postgres-primary", "query_latency_p95_ms", 2450),
        ("postgres-primary", "cpu_usage", 0.93),
        ("order-service", "api_latency_p95_ms", 29400),
        ("order-service", "error_rate", 0.11),
    ),
)


# --- password reset SMTP failure ----------------------------------------------------------------------------


def _smtp_failure_logs(rng: random.Random, start: datetime, end: datetime) -> Iterator[LogRecord]:
    yield LogRecord(
        start - timedelta(minutes=1),
        "notification-service",
        "INFO",
        "Configuration reloaded",
        {"changed": "smtp_credentials", "source": "secrets-manager"},
    )
    for ts in _per_minute(rng, start, end, rate=0.9):
        user = f"usr_{rng.randint(1000, 9999)}"
        yield LogRecord(ts, "auth-service", "INFO", "Password reset requested", {"user": user})
        yield LogRecord(
            ts + timedelta(milliseconds=180),
            "notification-service",
            "ERROR",
            "SMTPAuthenticationError",
            {"code": 535, "detail": "5.7.8 Username and Password not accepted", "relay": "smtp.mailrelay.io"},
        )
        yield LogRecord(
            ts + timedelta(milliseconds=200),
            "auth-service",
            "ERROR",
            "POST /auth/password-reset 500",
            {"user": user, "cause": "NotificationDeliveryError"},
        )


SMTP_AUTH_FAILURE = Scenario(
    key="smtp_auth_failure",
    title="HTTP 500 on password reset",
    root_cause_service="notification-service",
    symptom_service="auth-service",
    severity="SEV2",
    signature="SMTPAuthenticationError",
    summary="Rotated SMTP credentials were not accepted by the relay; password-reset e-mails fail with HTTP 500.",
    reports=(
        CustomerReport(
            "Users are receiving HTTP 500 when resetting passwords",
            "Support received 12 chats in 20 minutes: 'Forgot password' shows an error page.",
            "P2",
        ),
    ),
    logs=_smtp_failure_logs,
    metric_peaks=(
        ("notification-service", "error_rate", 0.58),
        ("auth-service", "error_rate", 0.045),
    ),
)


SCENARIOS: dict[str, Scenario] = {s.key: s for s in (PAYMENT_GATEWAY_TIMEOUT, DB_POOL_EXHAUSTION, SMTP_AUTH_FAILURE)}


def metric_override(scenario: Scenario, start: datetime, end: datetime, ramp_minutes: int = 3) -> MetricOverride:
    """Distort the scenario's metrics inside [start, end), ramping linearly to the peak."""
    peaks = {(svc, metric): peak for svc, metric, peak in scenario.metric_peaks}

    def override(service: str, metric: str, ts: datetime, healthy: float) -> float | None:
        peak = peaks.get((service, metric))
        if peak is None or not (start <= ts < end):
            return None
        progress = min(1.0, (ts - start) / timedelta(minutes=ramp_minutes))
        return healthy + (peak - healthy) * progress

    return override

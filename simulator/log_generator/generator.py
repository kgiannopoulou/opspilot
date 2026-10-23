"""Background ("healthy") log traffic for every ShopStream service."""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import datetime, timedelta

from simulator.records import LogRecord


def order_id(rng: random.Random) -> str:
    return f"ORD-{rng.randint(10000, 99999)}"


def trace_id(rng: random.Random) -> str:
    return f"{rng.getrandbits(64):016x}"


def checkout_flow(rng: random.Random, ts: datetime) -> list[LogRecord]:
    """A successful checkout: order-service → payment-service → gateway → notification."""
    oid, tid = order_id(rng), trace_id(rng)
    amount = round(rng.uniform(9, 420), 2)
    gateway_ms = int(rng.gauss(900, 120))
    return [
        LogRecord(ts, "order-service", "INFO", "Checkout started", {"order_id": oid}, tid),
        LogRecord(
            ts + timedelta(milliseconds=40),
            "payment-service",
            "INFO",
            "Payment request initiated",
            {"order_id": oid, "amount": amount, "currency": "EUR"},
            tid,
        ),
        LogRecord(
            ts + timedelta(milliseconds=60),
            "payment-service",
            "INFO",
            "Calling payment gateway",
            {"provider": "stripe"},
            tid,
        ),
        LogRecord(
            ts + timedelta(milliseconds=60 + gateway_ms),
            "payment-service",
            "INFO",
            "Payment authorised",
            {"order_id": oid, "gateway_ms": gateway_ms},
            tid,
        ),
        LogRecord(
            ts + timedelta(milliseconds=120 + gateway_ms),
            "order-service",
            "INFO",
            "Order confirmed",
            {"order_id": oid},
            tid,
        ),
        LogRecord(
            ts + timedelta(milliseconds=200 + gateway_ms),
            "notification-service",
            "INFO",
            "Email queued",
            {"template": "order_confirmation", "order_id": oid},
            tid,
        ),
    ]


def login_flow(rng: random.Random, ts: datetime) -> list[LogRecord]:
    user = f"usr_{rng.randint(1000, 9999)}"
    if rng.random() < 0.06:
        return [LogRecord(ts, "auth-service", "WARN", "Login failed: invalid credentials", {"user": user})]
    return [
        LogRecord(ts, "auth-service", "INFO", "Login succeeded", {"user": user, "mfa": rng.random() < 0.3}),
    ]


def dashboard_flow(rng: random.Random, ts: datetime) -> list[LogRecord]:
    merchant = f"m_{rng.randint(100, 999)}"
    ms = int(rng.gauss(180, 30))
    return [
        LogRecord(
            ts,
            "order-service",
            "INFO",
            "GET /merchant/dashboard 200",
            {"merchant": merchant, "duration_ms": ms},
        )
    ]


def password_reset_flow(rng: random.Random, ts: datetime) -> list[LogRecord]:
    user = f"usr_{rng.randint(1000, 9999)}"
    return [
        LogRecord(ts, "auth-service", "INFO", "Password reset requested", {"user": user}),
        LogRecord(
            ts + timedelta(milliseconds=150),
            "notification-service",
            "INFO",
            "Email sent",
            {"template": "password_reset", "smtp_ms": int(rng.gauss(240, 40))},
        ),
    ]


def housekeeping(rng: random.Random, ts: datetime) -> list[LogRecord]:
    return [
        LogRecord(
            ts,
            "postgres-primary",
            "INFO",
            "checkpoint complete",
            {"buffers_written": rng.randint(800, 4000), "write_s": round(rng.uniform(0.3, 2.5), 2)},
        )
    ]


# (flow, average occurrences per minute)
FLOWS = (
    (checkout_flow, 2.0),
    (login_flow, 3.0),
    (dashboard_flow, 1.5),
    (password_reset_flow, 0.4),
    (housekeeping, 0.2),
)


def generate_background_logs(rng: random.Random, start: datetime, end: datetime) -> Iterator[LogRecord]:
    """Emit healthy traffic minute by minute; event counts per minute follow the FLOWS rates."""
    minute = start
    while minute < end:
        for flow, rate in FLOWS:
            count = int(rate) + (1 if rng.random() < rate - int(rate) else 0)
            for _ in range(count):
                offset = timedelta(seconds=rng.uniform(0, 59))
                yield from flow(rng, minute + offset)
        minute += timedelta(minutes=1)

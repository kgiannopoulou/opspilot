"""ShopStream business data (payments, orders, password resets) derived from the simulated logs.

Investigation queries need application tables to run against. Deriving the rows from the same log stream keeps
them consistent with what the evidence engine sees: every GatewayTimeoutException in the logs is a TIMEOUT payment
here, every failed password-reset request a FAILED row, so a query result can confirm (or contradict) the logs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from simulator.records import LogRecord


def _stable(value: str, modulo: int) -> int:
    return int(hashlib.blake2b(value.encode(), digest_size=4).hexdigest(), 16) % modulo


def derive_app_data(logs: Iterable[LogRecord]) -> dict[str, list[dict[str, Any]]]:
    logs = sorted(logs, key=lambda log: log.timestamp)
    payments: dict[str, dict[str, Any]] = {}
    orders: dict[str, dict[str, Any]] = {}
    resets: list[dict[str, Any]] = []
    pending_resets: dict[str, dict[str, Any]] = {}

    for log in logs:
        attrs = log.attributes
        oid = attrs.get("order_id")
        if log.message == "Checkout started" and oid:
            orders[oid] = {
                "id": oid,
                "merchant_id": f"m_{100 + _stable(oid, 900)}",
                "status": "PENDING_PAYMENT",
                "total": round(9 + _stable(oid + "t", 41100) / 100, 2),
                "created_at": log.timestamp,
            }
        elif log.message == "Payment request initiated" and oid:
            payments[oid] = {
                "order_id": oid,
                "provider": "stripe",
                "amount": attrs.get("amount") or round(9 + _stable(oid + "a", 41100) / 100, 2),
                "currency": attrs.get("currency", "EUR"),
                "status": "PENDING",
                "gateway_ms": None,
                "error": None,
                "created_at": log.timestamp,
                "updated_at": log.timestamp,
            }
        elif log.message == "Payment authorised" and oid in payments:
            payments[oid] |= {"status": "CAPTURED", "gateway_ms": attrs.get("gateway_ms"), "updated_at": log.timestamp}
        elif log.message == "GatewayTimeoutException" and oid in payments:
            timeout_ms = int(str(attrs.get("timeout", "5000ms")).removesuffix("ms"))
            payments[oid] |= {"status": "TIMEOUT", "gateway_ms": timeout_ms, "error": log.message,
                              "updated_at": log.timestamp}  # fmt: skip
        elif log.message == "Order confirmed" and oid in orders:
            orders[oid]["status"] = "CONFIRMED"
        elif log.message == "Payment failed" and oid in orders:
            orders[oid]["status"] = "PAYMENT_FAILED"
        elif log.message == "Password reset requested":
            row = {"user_ref": attrs.get("user"), "status": "SENT", "error": None, "created_at": log.timestamp}
            resets.append(row)
            pending_resets[str(attrs.get("user"))] = row
        elif log.message == "POST /auth/password-reset 500":
            row = pending_resets.get(str(attrs.get("user")))
            if row is not None and log.timestamp - row["created_at"] < timedelta(seconds=5):
                row |= {"status": "FAILED", "error": attrs.get("cause")}

    return {
        "orders": list(orders.values()),
        "payments": list(payments.values()),
        "password_reset_requests": resets,
    }

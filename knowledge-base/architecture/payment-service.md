---
title: Payment Service Architecture
service: payment-service
tags: [payments, architecture, gateway, stripe, timeout]
---

# Payment Service Architecture

`payment-service` (team **Payments**, tier 1) authorises and captures card payments for orders.

## Request path

1. `order-service` calls `POST /payments` with the order id as **idempotency key**.
2. `payment-service` writes a `payments` row with status `PENDING` in `postgres-primary`.
3. It calls the external gateway (provider `stripe`) with a **5 s client timeout**.
4. On success the row becomes `CAPTURED`; on timeout `TIMEOUT` (reconciled later); on decline `DECLINED`.

## Key configuration

| Setting | Default | Notes |
|---|---|---|
| `PAYMENT_GATEWAY_TIMEOUT_MS` | 5000 | Hot-reloadable; must stay below the order-service checkout timeout (8000) |
| `PAYMENT_MAX_RETRIES` | 1 | Retries reuse the idempotency key |
| `payments.failover` flag | off | Routes traffic to the secondary provider |

## Signals

- `api_latency_p95_ms`: normal ≈ 1 s (dominated by the gateway call).
- `error_rate`: normal < 1 %.
- Logs: `Payment request initiated`, `Calling payment gateway`, `GatewayTimeoutException`, `CardDeclined`.

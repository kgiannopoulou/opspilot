---
title: Payment Gateway Timeout Runbook
service: payment-service
tags: [payments, gateway, timeout, GatewayTimeoutException, stripe]
---

# Payment Gateway Timeout Runbook

**Signature:** `GatewayTimeoutException provider=stripe timeout=5000ms` in `payment-service` logs.
**Customer symptom:** checkout freezes after clicking *Pay*, then shows a generic payment error.

## Triage (first 5 minutes)

1. Check the provider status page (status.stripe.com) and the `#payments-alerts` channel.
2. Confirm the blast radius: `payment-service` p95 latency and error rate on the dashboard. A gateway problem raises
   payment latency to roughly the client timeout (5 s) while **database latency stays normal**.
3. Check whether the failures are limited to one provider, region or card network (`provider` attribute in logs).

## Mitigation

| Situation | Action |
|---|---|
| Provider confirms degradation | Post a status-page notice; keep retries **idempotent** (`Idempotency-Key` = order id) |
| p95 just above 5 s | Temporarily raise `PAYMENT_GATEWAY_TIMEOUT_MS` to 10000 via config (no deploy needed) |
| Provider hard down > 15 min | Enable the secondary provider with the `payments.failover` feature flag |

Never retry a payment without the idempotency key: it can double-charge customers.

## Verification

- `GatewayTimeoutException` rate back to zero for 15 minutes.
- Orders stuck in `PAYMENT_PENDING` reconciled with the reconciliation job (`make reconcile-payments`).

## Follow-up

- Attach the provider incident reference to the OpsPilot incident.
- Review the timeout budget: the client timeout must stay below the order-service checkout timeout (8 s).

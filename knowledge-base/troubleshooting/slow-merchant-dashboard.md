---
title: Slow Merchant Dashboard Troubleshooting
service: order-service
tags: [dashboard, latency, database, connection-pool, QueuePool]
---

# Slow Merchant Dashboard Troubleshooting

The merchant dashboard (`GET /merchant/dashboard`) aggregates orders, revenue and payouts. Normal p95 is ~250 ms.

## Symptom → cause

| Observation | Meaning |
|---|---|
| ~30 s responses, some `QueuePool limit` errors | The app waits the full 30 s pool timeout: connections are exhausted |
| Slow only for very large merchants | Missing pagination / index for that merchant's order volume |
| Slow after a deploy | Regression in the dashboard query: compare query plans |

## Checks

1. `order-service` error log: count `QueuePool limit` per minute.
2. `postgres-primary`: `database_connections`, `query_latency_p95_ms`, slow-statement log.
3. Scheduled jobs started in the last hour (`Scheduled job started` in `order-service` logs).

See also: Database Connection Pool Exhaustion Runbook.

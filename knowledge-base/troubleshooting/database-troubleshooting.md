---
title: Database Troubleshooting Guide
service: postgres-primary
tags: [database, postgres, slow-queries, connection-pool, QueuePool, locks]
---

# Database Troubleshooting Guide

Symptom-first guide for `postgres-primary` (PostgreSQL 16, team **Platform**). Use it when an application service
reports slow responses or connection errors and you need to decide whether the database is the cause or a victim.

## Is the database the cause?

1. Compare `query_latency_p95_ms` on `postgres-primary` against its baseline (normal ≈ 15 ms).
2. Compare `database_connections` against the pool budget (normal ≈ 40 of 100).
3. If both are normal while an application service is slow, the database is **not** the cause — look at the
   service's own external dependencies (payment gateway, SMTP relay) instead.

## Slow queries

- Look for `slow statement` warnings in the database logs; the `duration_ms` and `application` attributes say who
  ran it.
- Check the plan with `EXPLAIN (ANALYZE, BUFFERS)` on a replica, never on the primary during an incident.
- Reporting queries (`GROUP BY` over `order_items`) must run on the read replica.

## Connection pool exhaustion

Application services log `QueuePool limit of size 20 overflow 10 reached` when they wait for a connection. The
usual cause is a few long-running statements holding connections, not too much traffic. Follow the Database
Connection Pool Exhaustion Runbook.

## Lock contention

`LockNotAvailable` errors and growing `Lock` wait events in `pg_stat_activity` point at hot rows (inventory during
flash sales). Prefer `SELECT … FOR UPDATE SKIP LOCKED` for reservation queues.

## Read-only diagnostics

These are safe to run during an incident:

```sql
SELECT pid, application_name, state, now() - query_start AS runtime, left(query, 120) AS query
FROM pg_stat_activity
WHERE state <> 'idle'
ORDER BY runtime DESC
LIMIT 20;
```

Do not run `VACUUM FULL`, `REINDEX` or `pg_terminate_backend` without the incident commander's approval.

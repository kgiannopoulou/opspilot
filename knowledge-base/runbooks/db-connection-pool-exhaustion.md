---
title: Database Connection Pool Exhaustion Runbook
service: postgres-primary
tags: [database, postgres, connection-pool, QueuePool, slow-query]
---

# Database Connection Pool Exhaustion Runbook

**Signature:** `TimeoutError: QueuePool limit of size 20 overflow 10 reached` in application logs;
`database_connections` close to `max_connections` (100) on `postgres-primary`.

## Triage

1. Find who holds the connections:

   ```sql
   SELECT pid, usename, application_name, state, now() - query_start AS runtime, left(query, 120) AS query
   FROM pg_stat_activity
   WHERE datname = 'shopstream' AND state <> 'idle'
   ORDER BY runtime DESC
   LIMIT 20;
   ```

2. Look for long-running analytical queries (`GROUP BY`, `Seq Scan` in the slow-statement log) and scheduled jobs that
   started shortly before the incident.
3. Check `query_latency_p95_ms` and CPU on the primary: exhaustion caused by slow queries shows both rising.

## Mitigation

- Cancel the offending queries with `SELECT pg_cancel_backend(pid)`; use `pg_terminate_backend` only if cancel fails.
- Pause the scheduled job (`jobs pause <name>`) and move reporting workloads to the read replica.
- Do **not** raise `max_connections` during the incident: it increases memory pressure and hides the cause.

## Follow-up

- Add the missing index (check `EXPLAIN (ANALYZE, BUFFERS)` for sequential scans on large tables).
- Set `statement_timeout` for reporting roles.

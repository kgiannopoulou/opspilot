# ADR 003 — Run AI work in Redis Streams background workers

**Status:** accepted · **Date:** 2026-10-25

## Context

An investigation runs log and metric queries, vector retrieval, an LLM write-up and query planning: 30 ms with the
deterministic writer, 5–30 s with a real model. Ticket triage calls the model too. Holding an HTTP request open for
that ties up API workers, breaks behind proxies with 30 s timeouts, and loses the work if the API restarts. The work
must survive crashes, not run twice, retry transient failures, and keep the API answering when Redis is down.

## Decision

A small job queue on **Redis Streams** with a dedicated **worker** process
([`workers/queue.py`](../../backend/app/workers/queue.py), ~100 lines):

- The API writes a `background_jobs` row in PostgreSQL (the source of truth for status, attempts, errors, timings),
  `XADD`s the id to `opspilot:jobs`, and returns **202** in ~60 ms.
- Workers read with a consumer group (`XREADGROUP`: each message to exactly one worker) and `XACK` when done.
- A crashed worker's messages are reclaimed after 60 s with `XAUTOCLAIM`; failures retry 3× with exponential backoff
  (sorted-set delay queue), then go to a dead-letter stream.
- Handlers are idempotent: finished jobs and investigations are skipped on redelivery.
- If Redis is unreachable the API runs the job in-process after the response (FastAPI `BackgroundTasks`), so the
  product degrades instead of failing.

## Alternatives considered

| Option | Why not |
|---|---|
| Celery | Heavyweight for two job types; broker + result backend configuration; prefork pool does not run on Windows dev machines; its retry/ack semantics would still need the PostgreSQL job table for the UI |
| RQ | Simple, but uses `fork()` (no Windows), Redis lists without consumer groups or reclaiming of a crashed worker's jobs |
| Dramatiq / Arq | Reasonable; one more dependency and abstraction for semantics that fit in one file |
| PostgreSQL as the queue (`SELECT … FOR UPDATE SKIP LOCKED`) | Viable and fewer moving parts; rejected because Redis is already present (cache, rate limits, live updates) and streams give consumer groups, blocking reads and pub/sub without polling the database |
| Kafka / RabbitMQ | Built for far higher throughput; operating either is out of proportion here |
| Synchronous requests | Fails the requirements above |

## Consequences

- The UI shows live per-stage progress ([ADR 004](004-use-sse-over-websockets.md)) and admins see queue depth,
  retries and dead letters (`GET /jobs`, Prometheus `opspilot_queue_messages`).
- `docker compose up --scale worker=3` scales horizontally; SIGTERM lets the current job finish.
- The queue is ours to maintain: delivery semantics are covered by tests (`test_workers.py`, `test_failures.py`)
  rather than by a library's reputation.
- Redis must run with AOF (`appendonly yes`) so queued jobs survive a Redis restart; PostgreSQL remains authoritative
  either way.

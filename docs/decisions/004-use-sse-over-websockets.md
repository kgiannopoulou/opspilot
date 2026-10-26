# ADR 004 — Use Server-Sent Events, not WebSockets, for live progress

**Status:** accepted · **Date:** 2026-10-26 · **Replaces:** 1-second polling of the investigation endpoint (Step 16)

## Context

While a worker runs an investigation ([ADR 003](003-use-redis-workers.md)), the incident page shows each stage as it
happens: *Search logs & metrics ✓ → Retrieve runbooks … → Save result*. Step 16 implemented this by polling
`GET …/investigations/{id}` every second: simple, but one request (and one database read) per viewer per second, and
up to a second of latency per stage. The traffic is strictly **one-way** (server → browser), low-volume (a handful of
events per investigation), short-lived (seconds to a minute), and must pass through the Next.js proxy with the
same bearer-token authentication as every other API call.

## Decision

Stream progress with **Server-Sent Events**: `GET /api/v1/incidents/{n}/investigations/{id}/events`
(`text/event-stream`).

- The worker commits each stage to PostgreSQL, then `PUBLISH`es the investigation id on a Redis channel.
- The SSE endpoint authorizes and resolves the investigation *before* the stream opens (a bad id is a plain 404),
  subscribes to the channel, re-reads the row on each notification and sends a `progress` event with the full
  investigation, then `done`. It also re-reads on a 2 s timer, so a lost notification or a Redis outage only costs
  latency. Comment heartbeats every 15 s keep idle proxies from closing the connection; streams end after 10 minutes.
- The browser reads the stream with `fetch()` + a 40-line parser ([`services/sse.ts`](../../frontend/services/sse.ts))
  because `EventSource` cannot send an `Authorization` header — the access token stays in memory, never in a URL or
  cookie. If the stream cannot be opened or drops, the hook falls back to polling every second.

## Alternatives considered

| Option | Why not |
|---|---|
| **WebSockets** | Bidirectional, which we do not need; a separate protocol upgrade through the Next.js proxy and any load balancer; authentication has to be reinvented (no headers on the browser `WebSocket` constructor → token in the URL or a first message); no automatic reconnection; a stateful connection manager on the server |
| Keep polling | Works, but N viewers × 1 req/s × DB read, and up to 1 s latency per stage; it remains as the fallback |
| Long polling | Lower latency than polling, but reinvents SSE with more edge cases |
| Push via a hosted service (Pusher, Ably) | External dependency and data leaving the platform for a feature HTTP already supports |

## Consequences

- Plain HTTP: same route, same auth dependency, same RBAC matrix test, same rate limits, same access logs and
  metrics. Proxies need only to not buffer (`X-Accel-Buffering: no`, `Cache-Control: no-cache`).
- Each open stream holds one connection and, between events, a thread waiting on Redis (the endpoint is a sync
  generator in FastAPI's thread pool). At tens of concurrent viewers this is fine; at thousands, an async
  implementation (`redis.asyncio`) or a dedicated gateway would replace it — the wire format would not change.
- HTTP/1.1 browsers allow ~6 connections per origin; one stream per open incident page is well inside that.
- If a future feature needs client → server messages in real time (collaborative editing of a post-mortem), that
  feature would get WebSockets; this decision covers one-way notifications.

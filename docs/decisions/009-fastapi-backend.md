# ADR 009 — FastAPI + SQLAlchemy 2 for the backend

**Status:** accepted · **Date:** 2026-10-23 · **Formerly:** ADR 0003

## Context

The backend serves a REST API, hosts the AI investigation layer and must integrate with the Python data/ML ecosystem
(embeddings, the Anthropic SDK, sqlglot, the simulator).

## Decision

FastAPI with Pydantic 2 schemas, SQLAlchemy 2 ORM (typed `Mapped[]` models, checked by mypy) and Alembic migrations.
Endpoints are plain `def` functions on synchronous sessions; FastAPI runs them in its thread pool.

## Alternatives considered

| Option | Why not |
|---|---|
| Django + DRF | Excellent admin and ORM, but heavier for an API-only service; async story and typed schemas less direct |
| Flask | More assembly required (validation, OpenAPI, dependency injection) |
| Node.js (NestJS) | Would split the language from the AI/data code |
| Async SQLAlchemy throughout | More complex code and testing for no measured benefit at this load; the SSE endpoint is the first place where async would pay off ([ADR 004](004-use-sse-over-websockets.md)) |

## Consequences

- OpenAPI documentation is generated at `/docs`; request validation and limits live in the schemas.
- Dependency injection (`Depends`) carries authentication, RBAC, rate limits and the database session — which is what
  lets the RBAC matrix test read every route's permission from the app itself.
- The investigation engine, the API and the evaluation suite share one language and one set of models.

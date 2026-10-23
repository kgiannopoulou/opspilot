# ADR 0003 — FastAPI + SQLAlchemy 2 for the backend

**Status:** accepted

## Context
The backend serves a REST API, will host the AI investigation layer and must integrate with the Python data/ML
ecosystem (embeddings, LLM SDKs).

## Decision
FastAPI with Pydantic 2 schemas, SQLAlchemy 2 ORM (typed `Mapped[]` models) and Alembic migrations.

## Consequences
- OpenAPI documentation is generated automatically at `/docs`.
- The investigation engine and the API share one language and one set of models.
- Synchronous SQLAlchemy sessions keep the code simple; endpoints are declared as plain `def` so FastAPI runs them in
  its thread pool.

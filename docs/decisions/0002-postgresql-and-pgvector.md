# ADR 0002 — PostgreSQL with pgvector as the single data store

**Status:** accepted

## Context
OpsPilot stores relational data (tickets, users, incidents), time-series-like data (logs, metric samples) and,
later, embeddings for semantic search over runbooks and past incidents.

## Decision
Use PostgreSQL 16 with the `pgvector` extension for everything, instead of adding a dedicated vector database
or a time-series database.

## Consequences
- Joins between incidents, evidence and documents are plain SQL, inside one transaction.
- One backup/restore story and one migration tool (Alembic).
- Log/metric volumes are limited to what a demo needs; a production deployment would read those from Loki/Prometheus
  and only persist the samples used as evidence.

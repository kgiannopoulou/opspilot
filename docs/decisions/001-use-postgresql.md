# ADR 001 — Use PostgreSQL as the system of record

**Status:** accepted · **Date:** 2026-10-23 · **Supersedes:** part of the former ADR 0002

## Context

OpsPilot stores strongly relational data — users, roles, tickets, incidents, the links between them, evidence rows
that cite log lines and document chunks — next to append-heavy operational data (application logs, per-minute metric
samples, background jobs) and an audit trail that must be tamper-evident. Most use cases change several of these at
once: resolving an incident updates the incident, writes its timeline event and its audit row. A support engineer
also needs ad-hoc, investigative queries (`GROUP BY status` over payments in a window), which is exactly what SQL is
for.

## Decision

Use **PostgreSQL 16** as the single system of record, managed by Alembic migrations, accessed through SQLAlchemy 2.

- Integrity lives in the schema: foreign keys with deliberate `ON DELETE` rules, `CHECK` constraints for every
  enumeration and range, unique constraints that encode business rules (one incident per ticket).
- Each use case is one transaction that commits the change, its timeline event and its audit row together.
- Database features carry weight: `JSONB` for variable log attributes and evidence data, BRIN on log timestamps,
  partial indexes for WARN/ERROR lines, `percentile_cont`/`date_bin`/`FILTER` for dashboard aggregates, row locks
  (`FOR UPDATE`) for concurrent ticket edits, triggers for the audit hash chain, roles and `SET TRANSACTION READ ONLY`
  for the AI query sandbox.

## Alternatives considered

| Option | Why not |
|---|---|
| MySQL / MariaDB | Weaker `JSONB`, no `percentile_cont`, no `pgvector` equivalent in the same engine, less expressive row-level security and roles for the SQL sandbox |
| MongoDB (document store) | The data is relational; multi-entity transactions and joins (incident ↔ tickets ↔ evidence ↔ chunks) would move into application code |
| Separate stores per data type (Postgres + Elasticsearch for logs + a TSDB for metrics) | Correct at scale, but three backup stories and no cross-store transactions for a system whose value is *correlating* those types |
| SQLite | No concurrent writers (API + workers), no roles, no extensions |

## Consequences

- One backup/restore, one migration tool, one connection pool; the evidence engine joins logs, metrics, incidents and
  documents in plain SQL.
- A test fails if ORM models and migrations drift (Alembic autogenerate comparison), so the schema stays the contract.
- Log and metric volume is limited to what investigations need. A production deployment would keep raw telemetry in
  Loki/Prometheus and persist only the samples used as evidence (the collectors are behind a repository interface).
- The SQL sandbox for AI-proposed queries ([ADR 007](007-ai-generated-sql-safety.md)) is possible *because* the
  application data lives in a schema PostgreSQL can lock down with a role.

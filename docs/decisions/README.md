# Architecture Decision Records

Each record states the **context**, the **decision**, the **alternatives** that were considered and why they lost, and
the **consequences** — including the ones that hurt. Records are not rewritten when a decision changes; a new record
supersedes the old one.

| ADR | Decision | Status |
|---|---|---|
| [001](001-use-postgresql.md) | PostgreSQL as the single system of record | accepted |
| [002](002-use-pgvector.md) | pgvector (HNSW, cosine) for semantic search, not a separate vector database | accepted |
| [003](003-use-redis-workers.md) | Redis Streams background workers for AI work, PostgreSQL as the job source of truth | accepted |
| [004](004-use-sse-over-websockets.md) | Server-Sent Events, not WebSockets, for live investigation progress | accepted |
| [005](005-rag-retrieval-strategy.md) | RAG retrieval: heading-aligned chunks, hybrid scoring, one passage per document, evidence first | accepted |
| [006](006-evidence-first-investigations.md) | Evidence-first investigations: the model explains, the evidence decides | accepted |
| [007](007-ai-generated-sql-safety.md) | AI-generated SQL: never automatic, AST allowlist, read-only least-privilege sandbox | accepted |
| [008](008-monorepo.md) | Monorepo | accepted |
| [009](009-fastapi-backend.md) | FastAPI + SQLAlchemy 2 | accepted |
| [010](010-prompt-injection-defense.md) | Layered prompt-injection defence: retrieved and user-written text is data | accepted |

Numbering was consolidated on 2026-10-26: the original six records (`0001`–`0006`) were split and renumbered so that
the storage, queue, transport and retrieval decisions read in order. The former `0002` (PostgreSQL and pgvector) became
001 and 002; the former `0005` became 005; the others kept their content under new numbers.

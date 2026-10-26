# ADR 007 — Running AI-generated SQL in a read-only sandbox

**Status:** accepted · **Date:** 2026-10-25 · **Formerly:** ADR 0006

## Context

During an incident the fastest way to size impact is often a query against the application database ("how many
payments timed out?"). A model can propose such queries, but generated SQL is untrusted input: it may be wrong, slow,
destructive, or read data it should not (OpsPilot's own users and password hashes live in the same cluster) — and a
prompt-injected document could try to steer it.

## Decision

- Proposals are **never executed automatically**. An engineer (`incidents:investigate`) runs them, optionally after
  editing; every run and every refusal is audited with the exact SQL.
- A validator parses the SQL into an AST (sqlglot, PostgreSQL dialect) and accepts only a single `SELECT` over an
  allowlist of tables and functions, enforcing a row limit.
- The validator is the first layer, not the only one. Execution uses a separate connection with
  `SET TRANSACTION READ ONLY`, `SET LOCAL ROLE opspilot_readonly` (can read only the `shopstream` schema), statement and
  lock timeouts, a row cap and an unconditional rollback. Stored verdicts and edited SQL are re-validated at run time.
- Queries embed the investigation window as literal timestamps instead of `NOW()`, so a re-run is reproducible.

## Alternatives considered

| Option | Why not |
|---|---|
| Regex / keyword blocklists | Comments, quoting, nesting and dialect features make them easy to evade |
| Execute automatically, validated | Removes the human from a decision with production impact; one validator bug away from an incident |
| Only curated templates, no model | Safe, but misses the questions the templates did not anticipate; templates remain the default |
| Text-to-SQL service with its own permissions model | External dependency holding production data access |

## Consequences

- A validator bug cannot become a data change or a data leak; a "validator bypassed" test proves the database layers
  alone refuse `DELETE`, `UPDATE`, reading password hashes and long `pg_sleep`s.
- Some legitimate queries are refused (table-valued functions, tables outside the allowlist); the allowlists are
  code-reviewed configuration.
- In production the sandbox would point at a read replica via its own DSN; role and transaction settings stay.

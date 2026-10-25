# ADR 0006 — Running AI-generated SQL

**Status:** accepted

## Context
During an incident the fastest way to size impact is often a query against the application database ("how many
payments timed out?"). A model can propose such queries, but generated SQL is untrusted input: it may be wrong,
slow, destructive, or read data it should not (OpsPilot's own users and password hashes live in the same cluster).

## Decision
- Proposals are **never executed automatically**. An engineer (`incidents:investigate`) runs them, optionally after
  editing; every run and every refusal is audited with the exact SQL.
- A validator parses the SQL into an AST (sqlglot, PostgreSQL dialect) and accepts only a single SELECT over an
  allowlist of tables and functions, enforcing a row limit. Regular expressions were rejected: comments, quoting
  and nesting make them easy to evade.
- The validator is the first layer, not the only one. Execution uses a separate connection with a read-only
  transaction, a dedicated `opspilot_readonly` role that can only read the `shopstream` schema, statement and lock
  timeouts, a row cap, and an unconditional rollback.
- Queries embed the investigation window as literal timestamps instead of `NOW()`, so a re-run is reproducible.

## Consequences
- A validator bug cannot become a data change or a data leak; tests simulate exactly that.
- Some legitimate queries are refused (table-valued functions, tables outside the allowlist). The allowlists are
  code-reviewed configuration, extended deliberately.
- In production the sandbox would point at a read replica of the application database via its own DSN; the role
  and transaction settings stay the same.

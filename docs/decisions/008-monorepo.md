# ADR 008 — Monorepo

**Status:** accepted · **Date:** 2026-10-23 · **Formerly:** ADR 0001

## Context

OpsPilot has a frontend, a backend, a data simulator and a knowledge base that evolve together. API contract changes
usually touch the backend and the UI in the same change; the evaluation suite needs the simulator, the backend and the
knowledge base at the same version.

## Decision

Keep all components in one repository with top-level folders `frontend/`, `backend/`, `simulator/`,
`knowledge-base/`, `infrastructure/` and `docs/`. CI runs one pipeline with per-component stages.

## Alternatives considered

| Option | Why not |
|---|---|
| Polyrepo (one repository per component) | Cross-repo version pinning for every API change; the AI benchmark would have to assemble three repositories at matching versions |
| Monorepo tooling (Nx, Turborepo, Bazel) | Useful for many packages; for two applications and a library it adds configuration without benefit |

## Consequences

- One pull request changes the API and its consumer atomically; one `docker-compose.yml` starts the whole system.
- CI must stay fast as the project grows: stages are ordered so lint and type errors fail in seconds, and path filters
  can be added per component when build time warrants it.

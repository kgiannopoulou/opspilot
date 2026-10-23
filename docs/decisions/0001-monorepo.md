# ADR 0001 — Monorepo

**Status:** accepted

## Context
OpsPilot has a frontend, a backend, a data simulator and a knowledge base that evolve together. API contract
changes usually touch the backend and the UI in the same change.

## Decision
Keep all components in one repository with top-level folders `frontend/`, `backend/`, `simulator/`,
`knowledge-base/`, `infrastructure/` and `docs/`. CI runs one job per component.

## Consequences
- One pull request can change the API and its consumer atomically.
- One `docker-compose.yml` brings up the whole system.
- CI must stay path-aware to remain fast as the project grows.

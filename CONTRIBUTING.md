# Contributing to OpsPilot

Thanks for your interest! OpsPilot is a portfolio project, but issues and pull requests are welcome.

## Local setup

```bash
# Infrastructure (Postgres + pgvector on 5438, Redis on 6381)
docker compose up -d postgres redis

# Backend
cd backend
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp ../.env.example .env
uvicorn app.main:app --reload --port 8010

# Frontend
cd frontend
npm install
npm run dev                                         # http://localhost:3010
```

## Repository layout

| Folder | Contents |
|---|---|
| `backend/app/api` | FastAPI routers |
| `backend/app/services` | Business rules (one transaction per use case) |
| `backend/app/repositories` | Database access |
| `backend/app/models` / `schemas` | ORM models / Pydantic contracts |
| `backend/app/auth` | JWT + role-based access control |
| `backend/app/ai` | Investigation engine |
| `simulator/` | ShopStream environment: logs, metrics, incident scenarios |
| `knowledge-base/` | Runbooks and troubleshooting guides |
| `infrastructure/` | Dockerfiles, Terraform (planned) |
| `docs/` | Architecture, decisions (ADRs), screenshots |

## Before opening a pull request

```bash
cd backend && ruff check . ../simulator && ruff format --check . ../simulator
pytest -m unit                       # seconds, no Docker
pytest --cov                         # everything (needs: docker compose up -d postgres redis); CI requires ≥ 90 %
cd frontend && npm run typecheck && npm test && npm run build
docker compose up -d --build && npx playwright test    # end-to-end, from frontend/
```

- Keep pull requests focused on one change.
- A new API route is picked up by `tests/test_rbac_matrix.py` automatically: it fails if the route has no
  authentication, or if a role without the permission gets past it.
- Schema changes need an Alembic migration (`alembic revision -m "..."`) — never edit an applied migration.
- Use [Conventional Commit](https://www.conventionalcommits.org/) style messages where practical.

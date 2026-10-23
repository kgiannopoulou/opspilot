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
cd backend && ruff check . ../simulator && ruff format --check . ../simulator && pytest
cd frontend && npm run typecheck && npm run build
```

- Keep pull requests focused on one change.
- Schema changes need an Alembic migration (`alembic revision -m "..."`) — never edit an applied migration.
- New endpoints need a permission check and a test for the forbidden case.
- Use [Conventional Commit](https://www.conventionalcommits.org/) style messages where practical.

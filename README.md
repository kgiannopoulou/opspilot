<div align="center">

# OpsPilot

### AI-Powered Application Support & Incident Intelligence

**Tickets → Logs → Metrics → AI Investigation → Resolution**

[Architecture](docs/architecture/README.md) · [Problem Statement](docs/problem-statement.md) · [API Docs](#api) · [Roadmap](#roadmap)

`FastAPI` · `Next.js` · `PostgreSQL` · `Redis` · `pgvector` · `Docker` · `GitHub Actions`

</div>

---

OpsPilot is an application support platform that correlates **support tickets**, **application logs** and
**service metrics** to help support engineers investigate incidents. Instead of a chatbot, it behaves like a
colleague who has already read the logs: every conclusion it draws is backed by evidence you can click through.

> **Status:** early development — the project is being built in public, step by step.

## The problem

ShopStream, a fictional SaaS company, runs an e-commerce and payment platform. Customers report symptoms —
*"payment fails after pressing Confirm Order"*, *"the dashboard takes 30 seconds to load"*, *"HTTP 500 on password
reset"* — and an Application Support Engineer has to walk from the ticket to logs, database, metrics, runbooks
and past incidents before a root cause emerges.

OpsPilot automates that evidence-gathering path and puts the result on one screen: a probable root cause,
a confidence score, the evidence behind it, next investigation steps and the relevant documentation.

→ Full write-up: [docs/problem-statement.md](docs/problem-statement.md)

## Architecture

![OpsPilot architecture](docs/architecture/architecture.svg)

A Next.js analyst UI talks to a FastAPI backend. PostgreSQL is the system of record (tickets, incidents, logs,
metrics, audit) and will hold the `pgvector` knowledge base; Redis provides caching and rate limiting.
The investigation engine correlates logs, metrics and past incidents into an evidence-backed root-cause report.

→ Components, flows and design principles: [docs/architecture](docs/architecture/README.md) ·
decisions: [docs/decisions](docs/decisions)

## ShopStream simulator

OpsPilot needs real evidence to investigate, so the repository ships a deterministic simulator of the fictional
ShopStream platform — `auth-service`, `payment-service`, `order-service`, `notification-service` and
`postgres-primary` — that emits realistic logs and per-minute metrics and can inject incidents:

```
2026-10-22T14:31:42Z INFO  payment-service  Payment request initiated order_id=ORD-84931
2026-10-22T14:31:42Z INFO  payment-service  Calling payment gateway provider=stripe
2026-10-22T14:31:47Z ERROR payment-service  GatewayTimeoutException provider=stripe timeout=5000ms order_id=ORD-84931
2026-10-22T14:31:47Z WARN  order-service  Payment failed order_id=ORD-84931 retry=1
```

| Scenario | Symptom | Metric footprint |
|---|---|---|
| `payment_gateway_timeout` | Checkout freezes after clicking Pay | payment p95 latency ≈ 5.3 s (+412 %), error rate 17 %, DB normal |
| `db_pool_exhaustion` | Dashboard takes 30 s to load | DB connections 99/100, query p95 2.4 s, order-service p95 29 s |
| `smtp_auth_failure` | HTTP 500 on password reset | notification error rate 58 %, auth-service error rate 4.5 % |

→ [simulator/README.md](simulator/README.md)

## Repository structure

```
opspilot/
├── frontend/            Next.js analyst UI (app/, components/, hooks/, services/, tests/)
├── backend/
│   ├── app/
│   │   ├── api/         FastAPI routers
│   │   ├── auth/        JWT + role-based access control
│   │   ├── models/      SQLAlchemy ORM models
│   │   ├── schemas/     Pydantic request/response contracts
│   │   ├── services/    business rules, one transaction per use case
│   │   ├── repositories/ database access
│   │   ├── ai/          investigation engine
│   │   ├── workers/     background jobs
│   │   └── core/        config, database, security
│   └── tests/
├── simulator/           ShopStream environment (log_generator/, metric_generator/, incidents/)
├── knowledge-base/      runbooks/, troubleshooting/, architecture/
├── infrastructure/      docker/ (Dockerfiles), terraform/ (planned)
├── docs/                architecture/, decisions/, screenshots/
├── .github/workflows/   CI: backend lint + tests, frontend typecheck + build, Docker build
├── docker-compose.yml
├── .env.example
├── CONTRIBUTING.md
└── LICENSE
```

## Getting started

```bash
docker compose up --build        # UI http://localhost:3010 · API http://localhost:8010/docs
```

Ports are deliberately non-default (Postgres 5438, Redis 6381, API 8010, UI 3010) so OpsPilot can run next to
other local stacks. Local development without Docker for the apps is described in [CONTRIBUTING.md](CONTRIBUTING.md).

## Roadmap

| # | Step | Status |
|---|------|--------|
| 1 | Product identity | ✅ |
| 2 | Problem definition | ✅ |
| 3 | Architecture design | ✅ |
| 4 | Repository structure | ✅ |
| 5 | Enterprise application simulator | ✅ |
| 6 | Ticket management system | ⏳ |
| 7 | Database design | ⏳ |
| 8 | Authentication & RBAC | ⏳ |
| 9 | Analyst dashboard | ⏳ |
| 10 | Incident investigation page | ⏳ |

## License

[MIT](LICENSE)

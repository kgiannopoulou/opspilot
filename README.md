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

## Ticket management

The platform is useful before any AI is involved. Tickets follow a support lifecycle enforced by a state machine
([`ticket_workflow.py`](backend/app/services/ticket_workflow.py)):

```
OPEN ──► TRIAGED ──► INVESTIGATING ──► RESOLVED ──► CLOSED
  │         ▲  │            ▲              │
  │         └──┘ (back)     └── reopen ────┘
  └──► CLOSED (duplicate / not a bug — resolution note required)
```

- Human-friendly numbers (`TCK-2026-00042`) from a Postgres sequence, priority P1–P4, affected service, labels.
- Assignment, public and internal comments, and an append-only **event timeline** (created, status changed,
  priority changed, assigned, commented) written in the same transaction as the change.
- Concurrent updates to one ticket are serialised with `SELECT … FOR UPDATE`.
- The API returns `allowed_transitions` with every ticket so the UI only offers valid actions.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/tickets?status=&priority=&service=&assignee_id=&unassigned=&q=&page=` | Filtered, paginated queue |
| `POST` | `/api/v1/tickets` | Create a ticket |
| `GET` / `PATCH` | `/api/v1/tickets/{number}` | Read / edit title, description, priority, service, labels |
| `POST` | `/api/v1/tickets/{number}/transition` | Move through the lifecycle |
| `POST` | `/api/v1/tickets/{number}/assign` | Assign / unassign |
| `GET` / `POST` | `/api/v1/tickets/{number}/comments` | Discussion |
| `GET` | `/api/v1/tickets/{number}/events` | Timeline |

Interactive OpenAPI docs: `http://localhost:8010/docs`.

## Database design

18 tables in PostgreSQL 16 + `pgvector`, managed by Alembic migrations:

```mermaid
erDiagram
    roles ||--o{ users : has
    users ||--o{ tickets : "reports / is assigned"
    services ||--o{ tickets : "affected by"
    tickets ||--o{ ticket_comments : has
    tickets ||--o{ ticket_events : timeline
    services ||--o{ service_dependencies : "depends on"
    incidents ||--o{ incident_tickets : groups
    tickets ||--o| incident_tickets : "belongs to ≤ 1"
    incidents ||--o{ incident_services : affects
    services ||--o{ application_logs : emits
    services ||--o{ metric_samples : emits
    knowledge_documents ||--o{ document_chunks : "split into (vector 384)"
    incidents ||--o{ ai_investigations : "investigated by"
    ai_investigations ||--o{ ai_evidence : "backed by"
    ai_evidence }o--o| knowledge_documents : cites
    users ||--o{ audit_events : performs
```

- **Integrity in the schema, not only in code:** foreign keys with deliberate `ON DELETE` rules, unique constraints
  (one incident per ticket, one metric value per series/minute), `CHECK` constraints for every enumeration,
  `confidence BETWEEN 0 AND 1`, no self-dependencies.
- **Indexes for real queries:** composite queue index, partial index on WARN/ERROR logs, BRIN on log timestamps,
  GIN on document tags, HNSW (`vector_cosine_ops`) on chunk embeddings.
- **JSONB only where justified** — variable log attributes, event payloads, engine-owned evidence data.
- **Transactions + audit:** each use case commits the change, its timeline event and its `audit_events` row together.
- A test fails if ORM models and migrations drift (`alembic` autogenerate comparison).

→ Full design notes and the complete ER diagram: [docs/architecture/database.md](docs/architecture/database.md)

The demo seed (`python -m app.scripts.seed --reset`) loads 60 days of incident history, ~300 tickets, and the last
6 hours of simulated logs/metrics with three live incidents.

## Authentication & access control

| Endpoint | Behaviour |
|---|---|
| `POST /api/v1/auth/login` | bcrypt-verified credentials → 15-minute **JWT access token** (HS256, algorithm pinned, issuer checked) in the body + **refresh token** in an `HttpOnly`, `SameSite=Strict` cookie scoped to `/api/v1/auth` |
| `POST /api/v1/auth/refresh` | **Rotates** the refresh token on every use. Presenting an already-rotated token is treated as theft: the whole token family is revoked |
| `POST /api/v1/auth/logout` | Revokes the session's refresh-token family and clears the cookie |
| `GET /api/v1/auth/me` | Current user, role and effective permissions |

Refresh tokens are opaque random strings stored only as SHA-256 hashes. Failed logins are throttled per
e-mail + IP in Redis (5 failures → 15-minute lock), error messages never reveal whether an e-mail exists, and login
timing is equalised for unknown users. Users are re-loaded on every request, so deactivating an account takes effect
immediately rather than when its JWT expires. Logins, failures, token-reuse detection and all user management
are written to `audit_events`.

**Role-based access control** — roles live in the database, permissions per role in
[`app/auth/permissions.py`](backend/app/auth/permissions.py), enforced with a `require(Permission.X)` dependency:

| Permission | Viewer | Support Agent | Engineer | Admin |
|---|:-:|:-:|:-:|:-:|
| `tickets:read`, `incidents:read`, `dashboard:read` | ✅ | ✅ | ✅ | ✅ |
| `tickets:write` — create/update/comment/assign | | ✅ | ✅ | ✅ |
| `incidents:investigate` — run investigations, link tickets | | | ✅ | ✅ |
| `users:manage` — `POST/PATCH/DELETE /users` | | | | ✅ |
| `services:manage`, `audit:read` | | | | ✅ |

`DELETE /api/v1/users/{id}` requires **admin**; it is a soft delete (history keeps its references) that also
revokes every refresh token. Admins cannot delete, deactivate or demote themselves.

Demo accounts (password `opspilot-demo`): `nikos@opspilot.dev` (admin), `maria@opspilot.dev` and
`alex@opspilot.dev` (engineer), `sam@opspilot.dev` (support agent), `viewer@opspilot.dev` (viewer).

## Analyst dashboard

A Next.js analyst UI in the spirit of Datadog / ServiceNow / PagerDuty: sign-in, a sidebar app shell that hides what
your role cannot use, and five working screens — **Dashboard**, **Incidents**, **Tickets** (queue + detail with
lifecycle actions, assignment, comments and timeline), **Services** and **Admin** (user management).

![OpsPilot dashboard](docs/screenshots/dashboard.jpg)

The main dashboard answers "what is on fire right now?" at a glance:

- **Active incidents by severity** (SEV1–SEV3), **resolved** count and **MTTR** for the last 30 days with the change
  against the previous 30 days, open / unassigned / today's ticket counts.
- **Active incident list** ordered by severity then age, with affected services, linked tickets and duration.
- **Incident volume** (stacked by severity) and **ticket volume** for the last 30 days — hand-rolled SVG charts with
  hover tooltips, no chart library.
- **Service health** for the last 6 hours: status derived from error rate and p95 latency against the service's own
  median baseline, an error-rate sparkline and the latency change in percent.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/dashboard/summary` | Everything the dashboard renders in one round trip — aggregated in SQL (`FILTER`, `date_bin`, `percentile_cont`) and cached in Redis for 15 s because every open dashboard polls it |
| `GET` | `/api/v1/incidents?state=active\|resolved\|all&severity=&page=` | Incident list, active first |

The cache fails open: if Redis is unavailable the summary is computed directly. The access token is kept in memory
only (never `localStorage`); a page reload restores the session through the `HttpOnly` refresh cookie, and the
Next.js server proxies `/api/*` so that cookie stays first-party.

## Incident investigation

The incident page is the showcase screen: everything an engineer needs to go from *"customers can't pay"* to a
probable root cause, without opening five tools.

![Incident investigation](docs/screenshots/incident-investigation.jpg)

![Recommended steps, documentation and suggested SQL](docs/screenshots/incident-recommendations.jpg)

**How the investigation engine works** ([`app/ai/engine.py`](backend/app/ai/engine.py), engine `rules-v1`):

1. **Scope:** the affected services plus their direct dependencies, over a window starting at onset (capped at 60
   minutes so a long-running incident is not polluted by later, unrelated ones). "Normal" is the 2 hours before onset.
2. **Evidence collectors** query what is stored in PostgreSQL:
   - *Logs:* WARN/ERROR lines grouped by service + message, compared with the baseline rate (new vs. pre-existing).
   - *Metrics:* p90 during the incident vs. median baseline for latency, error rate, DB connections, query latency, CPU.
   - *Dependency health:* a healthy dependency localises the fault (*"postgres-primary remains healthy"*); a sick one
     **contradicts** the hypothesis and lowers confidence.
   - *Change events:* config reloads, scheduled jobs and deployments in the 30 minutes before onset.
   - *History:* resolved incidents with a matching error signature (*"Similar to INC-106, 41 days ago"*).
   - *Knowledge base:* runbook passages found by semantic search (see [RAG](#retrieval-augmented-generation-rag)).
3. **Hypotheses** ([`hypotheses.py`](backend/app/ai/hypotheses.py)) are proposed by log signatures and scored *only*
   from the evidence: `confidence = (1 − Π(1 − w) over supporting) × Π(1 − w) over contradicting`, capped at 97 %.
   The best-supported one wins. Root-cause text, steps and SQL are templates filled from real log attributes
   (`provider=stripe`, `timeout=5000ms`), so they never state a value that is not in the data.
4. Nothing matches? The result is an honest low-confidence *unknown* (≤ 35 %) naming the strongest new signal.

Every run is stored as an immutable `ai_investigations` row with its ordered `ai_evidence` (stance, weight and the raw
numbers in JSONB), audited, and shown in the UI with one click to see the data behind each claim. Results on the
seeded scenarios:

| Incident | Diagnosis | Confidence | Key evidence |
|---|---|---|---|
| Payment checkout timeout | Gateway exceeds the 5 s client timeout | ~80 % | 35 × `GatewayTimeoutException`, p95 +407 %, DB healthy, similar INC |
| Merchant dashboard slow | DB connection pool exhaustion | ~94 % | `QueuePool limit`, DB connections 33 → 99, revenue-report job started 2 min before |
| HTTP 500 on password reset | SMTP authentication failure | ~68 % | `SMTPAuthenticationError` (535), SMTP credentials reloaded 1 min before onset |

The rest of the page: incident lifecycle actions (`INVESTIGATING → IDENTIFIED → MONITORING → RESOLVED`, resolving
requires a root cause, *Accept as root cause* copies the AI finding), per-minute telemetry charts with the onset
marked, linked customer tickets and a live tail of errors/warnings. The knowledge base (`knowledge-base/*.md`) is
loaded idempotently by content hash and browsable at `/knowledge`.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/incidents/{number}` | Incident with linked tickets, allowed transitions and the latest investigation |
| `POST` | `/api/v1/incidents/{number}/investigations` | Run the engine (`incidents:investigate` — engineers, admins) |
| `GET` | `/api/v1/incidents/{number}/investigations` | Investigation history |
| `POST` | `/api/v1/incidents/{number}/status` | Lifecycle transition (+ root cause) |
| `GET` | `/api/v1/incidents/{number}/telemetry` | Metric series of affected services, 1 h before onset → now |
| `GET` | `/api/v1/incidents/{number}/logs?limit=` | Recent WARN/ERROR lines since onset |
| `GET` | `/api/v1/knowledge`, `/api/v1/knowledge/{slug}` | Knowledge base |

The evidence engine itself is deliberately LLM-free: deterministic, testable and fast (~20 ms). Language models only
enter at the write-up stage, below, and may only explain evidence that is already stored (ADR 0004).

## Retrieval-augmented generation (RAG)

```
Incident ─► search query ─► embedding ─► pgvector HNSW (cosine) ─► re-rank ─► runbook passages ─┐
Logs · metrics · changes · history ─► evidence engine (scored hypothesis) ───────────────────────┴─► writer ─► investigation
```

- **Knowledge base** — 12 fictional ShopStream documents in [`knowledge-base/`](knowledge-base/): runbooks (payment
  gateway timeout, DB connection pool, SMTP rotation, high CPU), troubleshooting guides (checkout, password reset,
  slow dashboard, database), architecture notes (payment and authentication services) and the incident response
  process.
- **Chunking** ([`chunking.py`](backend/app/ai/chunking.py)) — split on `##`/`###` headings so every chunk belongs to one
  numbered section (`§2`, `§2.1`) that can be cited; long sections are split on paragraphs. The embedded text is
  prefixed with *document › heading* so "1. Check the status page" still means something on its own.
- **Embeddings** ([`embeddings.py`](backend/app/ai/embeddings.py)) — pluggable. The default `hashing-v1` is a
  deterministic, dependency-free feature-hashed embedding (camelCase splitting, stemming, an ops synonym table that
  maps *freezes → timeout*, *slow → latency*) so CI and laptops need no model download. `fastembed`
  (BAAI/bge-small-en-v1.5, `pip install -r requirements-ml.txt`) has the same 384 dimensions — switching is a config
  change, and because each chunk records the model that embedded it, a switch re-embeds everything automatically.
- **Storage & search** — `document_chunks.embedding vector(384)` with an HNSW `vector_cosine_ops` index. Retrieval
  ([`retrieval.py`](backend/app/ai/retrieval.py)) is hybrid: cosine similarity plus small, explainable boosts for the
  affected service's documents and for curated tags matching the error signature; at most two passages per document.
- **Query generation** — built from the incident title, the matched log signature and corroborating messages, and
  stored on the investigation (`search_query`) so retrieval can be audited. For *unknown* failure modes the query
  uses the strongest new log signal, so a runbook can still help where no hypothesis exists.
- **Writer** ([`writer.py`](backend/app/ai/writer.py)) — with `OPSPILOT_LLM_PROVIDER=anthropic` Claude writes the root
  cause, a short narrative and the next steps from the evidence and passages, through a JSON-schema constrained call
  ([`llm.py`](backend/app/ai/llm.py)). Without a key — or when the call fails — an extractive writer composes the
  same sections from the evidence summaries, so the product never depends on an LLM being up.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/knowledge/search?q=&k=&service=` | Semantic search over passages (the retrieval investigations use) |

`python -m app.scripts.reindex` syncs `knowledge-base/` (the API container runs it on start); only changed documents
are re-chunked and re-embedded.

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
│   │   ├── ai/          evidence engine, chunking, embeddings, retrieval, LLM writer
│   │   ├── workers/     background jobs
│   │   └── core/        config, database, security
│   └── tests/
├── simulator/           ShopStream environment (log_generator/, metric_generator/, incidents/)
├── knowledge-base/      runbooks/, troubleshooting/, architecture/, process/
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
| 6 | Ticket management system | ✅ |
| 7 | Database design | ✅ |
| 8 | Authentication & RBAC | ✅ |
| 9 | Analyst dashboard | ✅ |
| 10 | Incident investigation page | ✅ |
| 11 | RAG: knowledge base, chunking, embeddings, pgvector retrieval, LLM write-up | ✅ |

## License

[MIT](LICENSE)

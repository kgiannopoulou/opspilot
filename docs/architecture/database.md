# Database design

PostgreSQL 16 with the `pgvector` extension. The schema is owned by Alembic migrations in
[`backend/migrations/versions`](../../backend/migrations/versions); a test (`test_models_match_migrations`) fails if the
ORM models and the migrations ever drift apart.

## Entity-relationship diagram

```mermaid
erDiagram
    roles ||--o{ users : "has"
    users ||--o{ tickets : "reports"
    users ||--o{ tickets : "is assigned"
    services ||--o{ tickets : "affected by"
    tickets ||--o{ ticket_comments : "has"
    tickets ||--o{ ticket_events : "timeline"
    services ||--o{ service_dependencies : "depends on"
    incidents ||--o{ incident_tickets : "groups"
    tickets ||--o| incident_tickets : "belongs to ≤1"
    incidents ||--o{ incident_services : "affects"
    services ||--o{ incident_services : ""
    services ||--o{ incidents : "primary service"
    users ||--o{ incidents : "commands"
    services ||--o{ application_logs : "emits"
    services ||--o{ metric_samples : "emits"
    services ||--o{ knowledge_documents : "documented by"
    knowledge_documents ||--o{ document_chunks : "split into"
    incidents ||--o{ ai_investigations : "investigated by"
    ai_investigations ||--o{ ai_evidence : "backed by"
    ai_evidence }o--o| incidents : "similar incident"
    ai_evidence }o--o| knowledge_documents : "cites"
    users ||--o{ audit_events : "performs"

    roles {
        smallint id PK
        varchar name UK
    }
    users {
        bigint id PK
        varchar email UK
        varchar full_name
        bool is_active
        smallint role_id FK
    }
    services {
        bigint id PK
        varchar name UK
        smallint tier "CHECK 1-4"
        varchar owner_team
    }
    service_dependencies {
        bigint service_id PK,FK
        bigint depends_on_id PK,FK "CHECK <> service_id"
    }
    tickets {
        bigint id PK
        varchar number UK "TCK-2026-00042"
        varchar priority "CHECK P1-P4"
        varchar status "CHECK lifecycle"
        text_array labels
        bigint service_id FK
        bigint reporter_id FK
        bigint assignee_id FK
        timestamptz triaged_at
        timestamptz resolved_at
        timestamptz closed_at
    }
    ticket_comments {
        bigint id PK
        bigint ticket_id FK
        bigint author_id FK
        text body
        bool internal
    }
    ticket_events {
        bigint id PK
        bigint ticket_id FK
        varchar type
        varchar from_value
        varchar to_value
        jsonb data
    }
    incidents {
        bigint id PK
        varchar number UK "INC-421"
        varchar severity "CHECK SEV1-4"
        varchar status
        varchar signature
        timestamptz started_at
        timestamptz resolved_at
        text root_cause
    }
    incident_tickets {
        bigint incident_id PK,FK
        bigint ticket_id PK,FK,UK
    }
    incident_services {
        bigint incident_id PK,FK
        bigint service_id PK,FK
    }
    application_logs {
        bigint id PK
        timestamptz ts
        bigint service_id FK
        varchar level
        text message
        jsonb attributes
        varchar trace_id
    }
    metric_samples {
        bigint id PK
        timestamptz ts
        bigint service_id FK
        varchar name
        double value
    }
    knowledge_documents {
        bigint id PK
        varchar slug UK
        varchar category
        text_array tags
        text content
        varchar content_hash
    }
    document_chunks {
        bigint id PK
        bigint document_id FK
        int chunk_index
        text content
        vector_384 embedding
    }
    ai_investigations {
        bigint id PK
        bigint incident_id FK
        varchar status
        varchar engine
        text root_cause
        numeric confidence "CHECK 0-1"
        jsonb recommended_steps
        text suggested_sql
    }
    ai_evidence {
        bigint id PK
        bigint investigation_id FK
        varchar kind
        varchar stance
        text summary
        double weight
        jsonb data
    }
    audit_events {
        bigint id PK
        timestamptz occurred_at
        bigint actor_id FK
        varchar action
        varchar entity_type
        varchar entity_id
        jsonb changes
        inet ip_address
    }
```

## Table groups

| Group | Tables | Notes |
|---|---|---|
| Identity | `roles`, `users` | Roles are reference data inserted by the migration (admin, engineer, support_agent, viewer). |
| Support | `tickets`, `ticket_comments`, `ticket_events` | `ticket_events` is an append-only timeline written with every change. |
| Incidents | `incidents`, `incident_tickets`, `incident_services` | Many tickets → one incident; one incident → many affected services. |
| Catalogue | `services`, `service_dependencies` | Dependency graph used to check upstream/downstream health during investigations. |
| Observability | `application_logs`, `metric_samples` | Samples of platform telemetry, produced by the simulator. |
| Knowledge | `knowledge_documents`, `document_chunks` | Runbooks and guides; chunks carry `vector(384)` embeddings for RAG. |
| AI | `ai_investigations`, `ai_evidence` | Every conclusion is stored with the evidence rows behind it. |
| Compliance | `audit_events` | Who did what to which entity; written in the same transaction as the change. |

## Integrity rules enforced by the database

| Rule | Mechanism |
|---|---|
| A ticket belongs to at most one incident | `UNIQUE (incident_tickets.ticket_id)` |
| A service cannot depend on itself | `CHECK (service_id <> depends_on_id)` |
| Ticket numbers / incident numbers / e-mails / service names are unique | unique constraints; numbers come from dedicated sequences (`ticket_number_seq`, `incident_number_seq`) |
| Enumerations (priority, status, severity, log level, evidence kind…) only hold known values | `VARCHAR` + `CHECK` constraints (easier to evolve than native `ENUM` types) |
| Confidence is a probability | `CHECK (confidence BETWEEN 0 AND 1)` |
| One metric value per series and minute | `UNIQUE (service_id, name, ts)` |
| No orphaned data | Foreign keys with explicit `ON DELETE` behaviour: `CASCADE` for owned children (comments, events, chunks, evidence), `SET NULL` for optional references (assignee, service), `RESTRICT` where history must be kept (ticket reporter, user role) |

## Indexes and why they exist

| Index | Query it serves |
|---|---|
| `tickets (status, priority, created_at DESC)` | The ticket queue: filter by status, order by priority and recency |
| `application_logs (service_id, ts DESC)` | "Logs of service X in the incident window" — the investigation engine's main query |
| `application_logs (service_id, ts) WHERE level IN ('WARN','ERROR')` | Partial index: error lookups stay small because errors are rare |
| `application_logs USING brin (ts)` | Append-only, time-ordered data: a tiny index for range scans and retention deletes |
| `application_logs (trace_id) WHERE trace_id IS NOT NULL` | Follow one request across services |
| `incidents (status, severity)`, `incidents (started_at)` | Dashboard counters and trend charts |
| `incidents (signature)` | Similar-incident lookup |
| `knowledge_documents USING gin (tags)` | Tag matching (`tags && ARRAY[...]`) |
| `document_chunks USING hnsw (embedding vector_cosine_ops)` | Approximate nearest-neighbour retrieval for RAG |
| `audit_events (entity_type, entity_id)`, `(actor_id, occurred_at DESC)` | "History of this ticket", "what did this user do" |

## Where JSONB is used — and where it is not

JSONB is used only where the structure genuinely varies or belongs to another component's version:

- `application_logs.attributes` — every log line has different fields (`order_id`, `provider`, `timeout`…).
- `ticket_events.data`, `audit_events.changes` — the payload depends on the event type.
- `ai_investigations.recommended_steps`, `ai_evidence.data` — shape is owned by the investigation engine version.

Everything that is filtered, joined or constrained (status, priority, service, timestamps, foreign keys) is a real column.

## Transactions

Service-layer methods are units of work: the domain change, its `ticket_events` timeline row and its `audit_events`
row commit together or not at all. Declaring an incident with linked tickets is a single transaction — if one ticket
is unknown or already linked elsewhere, nothing is written (`test_declare_is_atomic`). Concurrent changes to the same
ticket are serialised with `SELECT … FOR UPDATE`.

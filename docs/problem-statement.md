# Problem Statement

## The fictional customer: ShopStream

OpsPilot is designed for **ShopStream**, a fictional mid-sized SaaS company that runs an e-commerce and payments
platform for ~2,000 online merchants. The platform is split into a handful of services:

| Service | Responsibility | Typical failure modes |
|---|---|---|
| `auth-service` | Login, sessions, password reset | SMTP outages, token-signing errors, brute-force lockouts |
| `payment-service` | Card payments through an external gateway (Stripe-like) | Gateway timeouts, declined-card spikes, idempotency bugs |
| `order-service` | Cart, checkout, order lifecycle | Retries storms, inventory lock contention |
| `notification-service` | E-mail / SMS / webhooks | Provider rate limits, queue backlog |
| `postgres-primary` | Shared relational database | Connection-pool exhaustion, slow queries, lock waits |

## What customers report

Customers never say *"the payment gateway p95 latency exceeds the 5 s client timeout"*. They say:

- *"Payment fails after pressing **Confirm Order**."*
- *"The dashboard takes 30 seconds to load after login."*
- *"Users are receiving HTTP 500 when resetting passwords."*

Those reports arrive as tickets written by merchants or by first-line customer support.

## How the investigation works today

An Application Support Engineer receiving one of those tickets walks a long, mostly manual path:

```
Ticket                        "Checkout freezes after clicking Pay"
  ↓
Application logs              grep payment-service logs around the reported time
  ↓
Database                      check failed/timed-out payments, connection pool
  ↓
Metrics                       latency, error rate, CPU, memory — is anything abnormal?
  ↓
Documentation                 find the right runbook (if it exists and is up to date)
  ↓
Past incidents                "didn't this happen in March?" — ask in Slack
  ↓
Determine root cause
  ↓
Recommend / apply fix
```

### Pain points

1. **Context switching** — five or six tools (ticketing, log search, metrics, wiki, SQL client, chat) for a single ticket.
2. **Tribal knowledge** — the link between *"checkout freezes"* and *"gateway timeout config"* lives in senior engineers' heads.
3. **Duplicate work** — ten tickets about the same outage get investigated ten times before someone links them to one incident.
4. **Slow time-to-root-cause** — most of the MTTR is spent *finding* the evidence, not fixing the problem.
5. **Unverifiable conclusions** — hand-offs say "probably the gateway" without the log lines or numbers that justify it.

## What OpsPilot does

OpsPilot automates the evidence-gathering part of that path and presents it on one screen:

| Manual step | OpsPilot |
|---|---|
| Read ticket, guess the service | Ticket is linked to an affected service and grouped into an incident |
| grep logs | Error signatures in the incident window are counted and ranked automatically |
| Look at dashboards | Metrics are compared against a pre-incident baseline (e.g. *"latency +412 %"*) |
| Check the database | Dependency health is checked (*"database latency remains normal"*) and a suggested SQL query is generated |
| Search the wiki | Relevant runbooks are attached |
| Ask in Slack | Similar past incidents are found and linked |
| Write the hand-off | A probable root cause with a confidence score and the evidence behind it |

The engineer stays in control: OpsPilot **proposes and cites**, the human **decides and resolves**.

## Users and roles

| Persona | Role in OpsPilot | Needs |
|---|---|---|
| First-line customer support | **Support Agent** | Create and update tickets, see if an incident is already open |
| Application support / SRE | **Engineer** | Triage, investigate incidents, run investigations, resolve |
| Team lead / platform owner | **Admin** | Manage users, roles and the service catalogue |
| Stakeholders, management | **Viewer** | Read-only dashboards and incident status |

## Goals

- Reduce **time-to-probable-root-cause** for common incident classes from tens of minutes to seconds.
- Make every AI conclusion **evidence-backed** — no claim without a log count, metric delta or document reference.
- Be **useful without AI**: ticket lifecycle, incident grouping and dashboards must work on their own.
- Keep an **audit trail** of who changed what and which investigation produced which recommendation.

## Non-goals (for now)

- Replacing a log platform (Elastic, Loki) or metrics platform (Prometheus, Datadog) — OpsPilot ingests a sample of their data.
- Automatically executing remediations — recommendations are read-only suggestions.
- Multi-tenant SaaS hosting of OpsPilot itself.

## Success metrics

| Metric | Why it matters |
|---|---|
| MTTR (mean time to resolve) | Primary business outcome |
| Tickets per incident | Shows duplicate tickets are being grouped |
| Investigation acceptance rate | Share of investigations whose root cause the engineer confirms |
| Evidence coverage | Share of conclusions that cite at least two independent signals (logs + metrics) |

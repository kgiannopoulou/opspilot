# ShopStream simulator

A deterministic simulator of the fictional ShopStream platform. It produces the raw material an Application
Support Engineer works with — **logs** and **metrics** from five services — and can inject realistic incidents into
that stream so OpsPilot has real evidence to investigate.

## Topology

```
                 ┌──────────────┐
  customers ───► │ order-service│──────────────┐
                 └──┬───────┬───┘              │
                    │       │                  ▼
        ┌───────────▼─┐   ┌─▼────────────┐   ┌──────────────────────┐
        │payment-svc  │   │ auth-service │──►│ notification-service │──► SMTP relay
        └──┬───────┬──┘   └──────┬───────┘   └──────────────────────┘
           │       │             │
           │       ▼             ▼
           │   ┌──────────────────────┐
           │   │   postgres-primary   │
           ▼   └──────────────────────┘
  payment gateway (stripe)
```

Defined in [`topology.py`](topology.py) with owner teams, tiers and healthy metric baselines.

## What it generates

| Generator | Output |
|---|---|
| `log_generator` | Healthy traffic: checkouts (traced across order → payment → notification), logins, dashboard loads, password resets, DB checkpoints |
| `metric_generator` | One sample per service and metric per minute (`api_latency_p95_ms`, `error_rate`, `requests_per_min`, `cpu_usage`, `memory_usage`; `database_connections`, `query_latency_p95_ms` for Postgres) with a daily load curve and noise |
| `incidents` | Failure scenarios that add their own log lines and ramp metrics towards a peak |

### Incident scenarios

| Key | Customer symptom | Root cause | Signature |
|---|---|---|---|
| `payment_gateway_timeout` | "Checkout freezes after clicking Pay" | Gateway slower than the 5 s client timeout | `GatewayTimeoutException` |
| `db_pool_exhaustion` | "Dashboard takes 30 seconds to load" | Reporting job with seq-scan queries holds DB connections | `QueuePool limit` |
| `smtp_auth_failure` | "HTTP 500 when resetting passwords" | Rotated SMTP credentials rejected by the relay | `SMTPAuthenticationError` |

Each scenario also carries the customer reports (ticket title, description, priority) it would cause.

## Usage

```bash
python -m simulator scenarios
python -m simulator tail --incident payment_gateway_timeout:20:30 --service payment-service --level WARN
python -m simulator generate --hours 6 --incident db_pool_exhaustion:60:25 --out simulator/output
```

`--incident <scenario>:<started N minutes ago>:<duration minutes>` can be repeated. The same `--seed` always
produces the same output.

```
2026-10-22T14:31:42Z INFO  payment-service  Payment request initiated order_id=ORD-84931
2026-10-22T14:31:42Z INFO  payment-service  Calling payment gateway provider=stripe
2026-10-22T14:31:47Z ERROR payment-service  GatewayTimeoutException provider=stripe timeout=5000ms order_id=ORD-84931
2026-10-22T14:31:47Z WARN  order-service  Payment failed order_id=ORD-84931 retry=1
```

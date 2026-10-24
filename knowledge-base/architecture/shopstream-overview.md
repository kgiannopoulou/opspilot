---
title: ShopStream Platform Overview
service:
tags: [architecture, overview, dependencies]
---

# ShopStream Platform Overview

| Service | Team | Tier | Depends on |
|---|---|---|---|
| auth-service | Identity | 1 | postgres-primary, notification-service |
| payment-service | Payments | 1 | postgres-primary, external gateway |
| order-service | Checkout | 1 | payment-service, auth-service, postgres-primary, notification-service |
| notification-service | Messaging | 2 | SMTP relay, webhooks |
| postgres-primary | Platform | 1 | none (max_connections = 100) |

Each application uses a connection pool of 20 + 10 overflow. All services log in a common structured format and
emit per-minute metrics (`api_latency_p95_ms`, `error_rate`, `requests_per_min`, `cpu_usage`, `memory_usage`).

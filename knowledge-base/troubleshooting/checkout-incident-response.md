---
title: Checkout Incident Response Guide
service: order-service
tags: [checkout, payments, customer-impact, gateway, timeout]
---

# Checkout Incident Response Guide

Checkout is ShopStream's revenue path: any checkout failure affecting more than 1 % of orders is at least **SEV2**,
more than 5 % is **SEV1**.

## Where checkout can fail

```
browser ─► order-service ─► payment-service ─► payment gateway (external)
                 │                 │
                 └──► postgres-primary ◄──┘
```

| Symptom | Most likely cause | First check |
|---|---|---|
| Spinner after *Pay*, then error | Payment gateway slow / down | `payment-service` latency vs. DB latency |
| Error immediately after *Pay* | Card declines, validation | `CardDeclined` count by card network |
| Slow everywhere, not only checkout | Database | `postgres-primary` connections and query latency |

## Communication

- SEV1: the incident commander posts an update every 30 minutes; customer support uses the macro "Payments delayed".
- Link every customer ticket to the incident so support sees the status without asking engineering.

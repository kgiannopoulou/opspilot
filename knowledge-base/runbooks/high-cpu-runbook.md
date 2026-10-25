---
title: High CPU Runbook
tags: [cpu, performance, latency, bcrypt, autoscaling, saturation]
---

# High CPU Runbook

**Alert:** `cpu_usage` above 85 % for 10 minutes on any ShopStream service.
**Customer symptom:** everything served by that service gets slower; timeouts appear once queues build up.

## Triage (first 5 minutes)

1. Is CPU high on **one** service or **all** of them? One service points at a code or config change; all of them
   points at traffic (bots, a campaign) or a noisy neighbour on the node.
2. Check deployments and configuration changes in the 30 minutes before the alert (`Configuration reloaded`,
   `Deployment finished`).
3. Compare request rate with CPU. CPU rising with **flat traffic** means each request became more expensive.

## Common causes

| Cause | Signal | Action |
|---|---|---|
| bcrypt cost factor raised | `auth-service` CPU and login latency up together, traffic flat | Revert the cost factor to 12 |
| Login or scraping storm | request rate up, many `Login failed` warnings | Enable rate limiting at the edge |
| Runaway regex or JSON parsing | CPU pinned on one pod after a deploy | Roll back the deploy |
| Missing index | high CPU on `postgres-primary` with `slow statement` logs | See the Database Troubleshooting Guide |

## Mitigation

- Scale out the service (`kubectl scale deploy/<service> --replicas=+2`) to buy time — it does not fix the cause.
- Roll back the last deploy or configuration change if it lines up with the onset.

## Verification

- CPU back under 60 % and p95 latency back at baseline for 15 minutes.

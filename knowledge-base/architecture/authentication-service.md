---
title: Authentication Service Architecture
service: auth-service
tags: [auth, login, password-reset, sessions, jwt, bcrypt, smtp]
---

# Authentication Service Architecture

`auth-service` (team **Identity**, tier 1) handles merchant and customer login, sessions and password resets.

## Request paths

1. **Login** — `POST /auth/login` verifies the bcrypt hash in `postgres-primary` (cost factor 12) and issues a
   signed session token. Normal p95 is around 120 ms; almost all of it is the bcrypt comparison.
2. **Password reset** — `POST /auth/password-reset` stores a single-use token in `password_reset_requests` and asks
   `notification-service` to send the e-mail **synchronously**. If the e-mail cannot be sent the endpoint returns
   HTTP 500 and the request row is marked `FAILED`.
3. **Session validation** — every API call checks the token signature with the active signing key. Keys rotate with
   a 24-hour overlap window; rotating without the overlap logs `InvalidSignatureError` and logs every user out.

## Dependencies

| Dependency | Used for | Failure looks like |
|---|---|---|
| `postgres-primary` | users, password hashes, reset tokens | slow logins, `QueuePool limit` warnings |
| `notification-service` | reset and MFA e-mails | `POST /auth/password-reset 500`, delayed MFA codes |

## Signals

- `api_latency_p95_ms`: normal ≈ 120 ms. A sudden doubling with flat traffic usually means a bcrypt cost change.
- `error_rate`: normal < 0.5 %. Failed logins (`Login failed: invalid credentials`) are WARN, not errors.
- `cpu_usage`: normal ≈ 35 %; bcrypt is CPU-bound, so login storms and cost changes show up here first.

## Known failure modes

- **SMTP credentials rotated** → password reset returns 500. See the SMTP Credential Rotation Runbook.
- **bcrypt cost raised** → login latency spike and high CPU. See the High CPU Runbook.
- **Signing key rotated without overlap** → sessions invalidated after deploy.

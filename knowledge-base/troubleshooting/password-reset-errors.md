---
title: Password Reset Errors
service: auth-service
tags: [auth, password-reset, email, smtp, http-500]
---

# Password Reset Errors

`POST /auth/password-reset` creates a reset token and asks `notification-service` to send the e-mail
**synchronously**. If the e-mail cannot be sent, the request fails with HTTP 500 (`NotificationDeliveryError`).

## Checks

1. `auth-service` logs: `POST /auth/password-reset 500`. The `cause` attribute tells you which dependency failed.
2. `notification-service` error rate and its SMTP errors (`SMTPAuthenticationError`, `SMTP 421` rate limiting).
3. Any recent change to SMTP credentials or relay configuration.

## Workaround

Support can trigger a reset link from the admin console (`Users → Send reset link`), which uses the webhook channel
instead of SMTP.

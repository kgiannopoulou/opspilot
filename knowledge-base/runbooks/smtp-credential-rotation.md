---
title: SMTP Credential Rotation Runbook
service: notification-service
tags: [email, smtp, credentials, SMTPAuthenticationError, secrets]
---

# SMTP Credential Rotation Runbook

**Signature:** `SMTPAuthenticationError code=535 5.7.8 Username and Password not accepted` in
`notification-service` logs, usually right after `Configuration reloaded changed=smtp_credentials`.

## Triage

1. Confirm a credential change: search `notification-service` logs for `Configuration reloaded` in the 30 minutes
   before the first error.
2. Check the secret version in secrets-manager (`smtp/relay`) and whether the relay (smtp.mailrelay.io) shows the
   new key as **active**. New keys can take up to 10 minutes to propagate.
3. Identify impacted flows: password reset (`auth-service` returns HTTP 500), order confirmations, webhooks.

## Mitigation

- Roll back to the previous secret version (`secrets rollback smtp/relay --to previous`), then reload the service.
- If the old key was revoked, generate a new key on the relay and store it. Never paste keys into tickets.

## Follow-up

- Rotation must use an overlap window: add the new key, verify, then revoke the old one.
- `auth-service` should queue password-reset e-mails instead of failing the HTTP request (see Password Reset Errors).

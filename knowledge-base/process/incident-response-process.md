---
title: Incident Response Process
tags: [incident, process, severity, communication, postmortem, escalation]
---

# Incident Response Process

How ShopStream runs incidents. Applies to every SEV1–SEV3 incident; SEV4 issues are handled as tickets.

## Severity levels

| Severity | Definition | Response |
|---|---|---|
| SEV1 | Core flow (checkout, login) down or failing for many customers | Page on-call immediately, status page within 15 min |
| SEV2 | Core flow degraded, or a secondary flow fully down | Page on-call, status page within 30 min |
| SEV3 | Minor degradation, workaround exists | Handle in business hours |
| SEV4 | Cosmetic or single-customer issue | Ticket only |

Customer tickets map to priorities the same way: P1 ↔ SEV1, P2 ↔ SEV2, P3 ↔ SEV3, P4 ↔ SEV4.

## Roles

- **Incident commander** — owns decisions and the timeline; does not debug.
- **Investigators** — engineers from the owning team (see the service catalogue).
- **Communications** — support lead; updates the status page and replies to linked tickets.

## Lifecycle

1. **Investigating** — declare the incident, link the customer tickets, run an OpsPilot investigation.
2. **Identified** — root cause confirmed by a human; an AI suggestion alone is not enough.
3. **Monitoring** — fix applied; watch the signals named in the runbook for 15–30 minutes.
4. **Resolved** — root cause recorded; linked tickets resolved with a customer-facing note.

## Escalation

Escalate to the owning team's lead if the cause is not understood within **30 minutes** of a SEV1, or 2 hours of
a SEV2. Payment-provider issues go to the provider's support channel with the incident number.

## After the incident

A blameless postmortem is due within 5 working days for SEV1 and SEV2. It must include the timeline, the root
cause, what detected it, and follow-up actions with owners.

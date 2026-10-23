# ADR 0004 — Evidence-first investigations

**Status:** accepted

## Context
An "AI root cause" that cannot be verified is worse than none: engineers either ignore it or, worse, act on a
hallucination during an outage.

## Decision
Investigations are built from **evidence collectors** that query stored logs, metrics, past incidents and the
knowledge base. Each piece of evidence is persisted as an `ai_evidence` row with its source and numbers. Hypotheses
are scored only from evidence; the confidence score is a function of supporting vs. contradicting evidence.
A language model, when added, will *summarise and rank* collected evidence — never introduce facts that are not in it.

## Consequences
- The engine produces useful output with no LLM configured (deterministic and testable).
- Every recommendation in the UI can show *why*.
- Novel failure modes with no matching signature get a low-confidence "unknown" result instead of a guess.

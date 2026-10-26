# ADR 006 — Evidence-first investigations

**Status:** accepted · **Date:** 2026-10-24 · **Formerly:** ADR 0004

## Context

An "AI root cause" that cannot be verified is worse than none: during an outage engineers either ignore it or act on a
hallucination. Support engineers need to see *why* a conclusion was drawn — the log lines, the metric change, the
healthy dependency that rules something out — and the conclusion has to be stable enough to test.

## Decision

Investigations are produced by **evidence collectors** and a **scored hypothesis library**, not by a model:

- Collectors query stored logs, metrics, dependency health, change events, incident history and the knowledge base.
  Each piece of evidence is persisted as an `ai_evidence` row with its stance (supports / contradicts / context),
  weight, citation key and raw numbers.
- Hypotheses are proposed by log signatures and scored *only* from evidence:
  `confidence = (1 − Π(1 − w) over supporting) × Π(1 − w) over contradicting`, capped at 97 %. Nothing matching gives
  an honest low-confidence *unknown* (≤ 35 %).
- A language model may only *explain* stored evidence ([ADR 005](005-rag-retrieval-strategy.md)); it never sets the
  hypothesis or the confidence.

## Alternatives considered

| Option | Why not |
|---|---|
| LLM agent with tools (query logs, query metrics, decide) | Non-deterministic, hard to test, expensive per run; the model's confidence is not calibrated; tool access widens the prompt-injection blast radius |
| Anomaly-detection ML model | Needs labelled incident history the demo does not have; explains *that* something is wrong, not *why* |
| Static alert → runbook mapping | No correlation across services; no "this is ruled out" evidence |

## Consequences

- Useful output with no LLM configured; deterministic and fast (~25 ms); measured by the evaluation suite (95 %
  correct root cause on 41 simulated incidents, 100 % "unknown" when nothing is wrong).
- Every claim in the UI links to the data behind it.
- New failure modes need a new hypothesis (code review), which is slower than prompting but testable; *unknown*
  results with their strongest new signal show where the library is missing coverage.

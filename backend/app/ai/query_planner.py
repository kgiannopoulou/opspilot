"""Propose diagnostic SQL for an investigation, and attach a safety verdict to every proposal.

Proposals come from the matched hypothesis (curated templates) and, with an LLM configured, from the model — which
is given the ShopStream schema and the evidence and asked what it would check next. Both go through the same
validator; a proposal that fails is stored as BLOCKED with its violations, so the UI can show *why* it will not run.
Nothing here executes SQL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.ai.engine import InvestigationResult
from app.ai.llm import LLMClient, LLMError
from app.ai.sql_guard import Verdict, validate
from app.models import Incident

log = logging.getLogger(__name__)
MAX_LLM_QUERIES = 2

SCHEMA_DOC = """ShopStream application database (PostgreSQL, read-only):
- orders(id text, merchant_id text, status text: PENDING_PAYMENT|CONFIRMED|PAYMENT_FAILED, total numeric,
  created_at timestamptz)
- payments(id bigint, order_id text -> orders.id, provider text, amount numeric, currency text,
  status text: PENDING|CAPTURED|TIMEOUT|DECLINED, gateway_ms int, error text, created_at timestamptz,
  updated_at timestamptz)
- password_reset_requests(id bigint, user_ref text, status text: SENT|FAILED, error text, created_at timestamptz)
- pg_stat_activity (PostgreSQL sessions)"""

SYSTEM_PROMPT = """You help an application support engineer investigate a live incident. Propose read-only \
PostgreSQL queries that would confirm or rule out the suspected root cause, or size the customer impact.

Rules: a single SELECT per query; only the tables listed in SCHEMA; filter by the incident window given; no writes, \
no locks, no administrative functions. Do not repeat the queries already proposed. Each query needs a short title \
and a one-sentence purpose saying what the result would show. Propose at most two queries; propose none if the \
existing ones already cover the question."""

QUERIES_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "purpose": {"type": "string"}, "sql": {"type": "string"}},
                "required": ["title", "purpose", "sql"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["queries"],
    "additionalProperties": False,
}


@dataclass
class Proposal:
    title: str
    purpose: str
    sql: str
    source: str
    verdict: Verdict


class QueryPlanner:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    def plan(self, incident: Incident, result: InvestigationResult) -> list[Proposal]:
        proposals = [
            Proposal(q.title, q.purpose, q.sql, f"template:{result.hypothesis_key}", validate(q.sql))
            for q in result.queries
        ]
        if self.llm is not None:
            proposals += self._from_llm(incident, result, proposals)
        return proposals

    def _from_llm(self, incident: Incident, result: InvestigationResult, existing: list[Proposal]) -> list[Proposal]:
        evidence = "\n".join(f"- [{e.ref}] {e.summary}" for e in result.evidence if e.ref)
        proposed = "\n\n".join(f"-- {p.title}\n{p.sql}" for p in existing) or "(none)"
        prompt = (
            f"{SCHEMA_DOC}\n\nINCIDENT {incident.number}: {incident.title}\n"
            f"WINDOW: {result.window_start.isoformat(timespec='seconds')} to "
            f"{result.window_end.isoformat(timespec='seconds')} (UTC)\n"
            f"SUSPECTED ROOT CAUSE: {result.root_cause}\n\nEVIDENCE:\n{evidence}\n\nALREADY PROPOSED:\n{proposed}"
        )
        try:
            data = self.llm.complete_json(SYSTEM_PROMPT, prompt, QUERIES_SCHEMA, max_tokens=4096)
        except LLMError as exc:
            log.warning("LLM query proposals unavailable for %s: %s", incident.number, exc)
            return []
        queries = [q for q in data.get("queries", []) if q.get("sql", "").strip()][:MAX_LLM_QUERIES]
        return [
            Proposal(
                q["title"].strip()[:200], q["purpose"].strip(), q["sql"].strip(), self.llm.name, validate(q["sql"])
            )
            for q in queries
        ]

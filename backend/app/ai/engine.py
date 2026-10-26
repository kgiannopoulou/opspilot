"""Rules-based investigation engine (rules-v1).

Evidence first (ADR 006): collectors query the stored logs, metrics, incident history and knowledge base; each
finding becomes an `Evidence` item with the numbers behind it. Every known hypothesis whose signature appears in the
logs is scored only from that evidence, and the best-supported one wins:

    confidence = (1 - Π(1 - w) over supporting evidence) × Π(1 - w) over contradicting evidence

so independent pieces of support compound, and any contradiction pulls the score down. No LLM is involved; a later
engine can summarise the same evidence in prose without being allowed to add facts.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.hypotheses import CHANGE_MARKERS, HYPOTHESES, UNKNOWN_STEPS, Hypothesis, QueryTemplate
from app.ai.retrieval import KnowledgeRetriever, build_query
from app.models import Incident, Service, service_dependencies
from app.models.enums import EvidenceKind, EvidenceStance
from app.repositories.evidence import EvidenceRepository, LogPattern

ENGINE_VERSION = "rules-v1"
BASELINE = timedelta(hours=2)  # how far back "normal" is measured
BASELINE_GAP = timedelta(minutes=5)  # keep pre-incident warm-up out of the baseline
CHANGE_LOOKBACK = timedelta(minutes=30)
# Root causes show at onset. Capping the window keeps later, unrelated incidents out of a long-running one's evidence.
ONSET_WINDOW = timedelta(minutes=60)
MAX_CONFIDENCE = 0.97
UNKNOWN_MAX_CONFIDENCE = 0.35


@dataclass(frozen=True)
class MetricRule:
    label: str
    weight: float
    anomalous: Any  # (baseline, current) -> bool
    fmt: Any  # value -> str


def _ms(v: float) -> str:
    return f"{v:,.0f} ms"


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


DOC_PREFIX = {"runbook": "RUNBOOK", "troubleshooting": "GUIDE", "architecture": "ARCH", "process": "PROCESS"}
METRIC_SHORT = {"api_latency_p95_ms": "latency", "error_rate": "error-rate", "query_latency_p95_ms": "query-latency",
                "database_connections": "connections", "cpu_usage": "cpu"}  # fmt: skip

METRIC_RULES: dict[str, MetricRule] = {
    "api_latency_p95_ms": MetricRule("p95 latency", 0.30, lambda b, c: c >= 2 * b, _ms),
    "error_rate": MetricRule("error rate", 0.25, lambda b, c: c >= 0.03 and c >= 3 * b, _pct),
    "query_latency_p95_ms": MetricRule("query p95 latency", 0.25, lambda b, c: c >= 2 * b, _ms),
    "database_connections": MetricRule("connections", 0.25, lambda b, c: c >= 1.5 * b, lambda v: f"{v:.0f}"),
    "cpu_usage": MetricRule("CPU", 0.10, lambda b, c: c >= 1.6 * b, lambda v: f"{v * 100:.0f}%"),
}


@dataclass
class Evidence:
    kind: EvidenceKind
    stance: EvidenceStance
    summary: str
    weight: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)
    related_incident_id: int | None = None
    document_id: int | None = None
    # Citation key (LOG-48392, METRIC-payment-service-latency, INC-106, RUNBOOK-payment-gateway-timeout §1):
    # unique within an investigation, and the only way a write-up may refer to a fact.
    ref: str = ""
    chunk_id: int | None = None


@dataclass
class InvestigationResult:
    hypothesis_key: str
    root_cause: str
    confidence: float
    recommended_steps: list[str]
    suggested_sql: str | None  # the first proposed query (kept for API compatibility)
    evidence: list[Evidence]
    window_start: datetime
    window_end: datetime
    search_query: str | None = None
    queries: list[QueryTemplate] = field(default_factory=list)  # filled templates, validated before storage
    # What the knowledge-base retrieval is scoped by (set by the winning hypothesis).
    emitter: str | None = None
    doc_tags: tuple[str, ...] = ()
    # Every hypothesis that matched, best first, with its confidence: the runner-ups an engineer may want to rule out.
    alternatives: list[tuple[str, float]] = field(default_factory=list)


class _Defaults(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return f"<{key}?>"


def _fill(template: str, values: dict[str, Any]) -> str:
    return template.format_map(_Defaults(values))


def score(evidence: list[Evidence]) -> float:
    support = 1 - math.prod(1 - e.weight for e in evidence if e.stance == EvidenceStance.SUPPORTS)
    against = math.prod(1 - e.weight for e in evidence if e.stance == EvidenceStance.CONTRADICTS)
    return round(min(MAX_CONFIDENCE, support * against), 3)


@dataclass
class _Context:
    incident: Incident
    start: datetime
    end: datetime
    services: dict[str, Service]  # investigation scope: affected services + their direct dependencies
    patterns: list[LogPattern]
    baseline_counts: dict[tuple[str, str], int]
    baseline_metrics: dict[tuple[str, str], float]
    current_metrics: dict[tuple[str, str], float]

    @property
    def minutes(self) -> float:
        return max(1.0, (self.end - self.start).total_seconds() / 60)

    def is_new(self, p: LogPattern) -> bool:
        """At least 5× the pre-incident rate (or absent before)."""
        before = self.baseline_counts.get((p.service, p.message), 0)
        baseline_minutes = (BASELINE - BASELINE_GAP).total_seconds() / 60
        return before == 0 or (p.count / self.minutes) >= 5 * (before / baseline_minutes)

    def novelty(self, p: LogPattern) -> float:
        before = self.baseline_counts.get((p.service, p.message), 0)
        return p.count - before * self.minutes / ((BASELINE - BASELINE_GAP).total_seconds() / 60)


class InvestigationEngine:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = EvidenceRepository(db)
        self.retriever = KnowledgeRetriever(db)

    # -- context ---------------------------------------------------------------------------------------------

    def _context(self, incident: Incident) -> _Context:
        start = incident.started_at
        latest = self.repo.latest_signal()
        end = min(incident.resolved_at or latest or start + ONSET_WINDOW, start + ONSET_WINDOW)
        end = max(end, start + timedelta(minutes=1))

        declared = incident.affected_services or ([incident.primary_service] if incident.primary_service else [])
        scope: dict[str, Service] = {}
        for svc in declared:
            scope[svc.name] = svc
            for dep in svc.dependencies:
                scope.setdefault(dep.name, dep)
        # Direct dependents too: a fault in a shared component (the database) is often only *logged* by its callers
        # ("QueuePool limit" comes from order-service), so an incident declared on the component must see them.
        for dependent in self.db.scalars(
            select(Service)
            .join(service_dependencies, service_dependencies.c.service_id == Service.id)
            .where(service_dependencies.c.depends_on_id.in_([s.id for s in declared]))
        ):
            scope.setdefault(dependent.name, dependent)
        ids = [s.id for s in scope.values()]

        b_start, b_end = start - BASELINE, start - BASELINE_GAP
        baseline_patterns = self.repo.log_patterns(ids, b_start, b_end)
        names = list(METRIC_RULES)
        return _Context(
            incident=incident,
            start=start,
            end=end,
            services=scope,
            patterns=self.repo.log_patterns(ids, start, end),
            baseline_counts={(p.service, p.message): p.count for p in baseline_patterns},
            baseline_metrics=self.repo.metric_percentile(ids, names, b_start, b_end, 0.5),
            # p90 rather than the median: an incident that already recovered still shows its peak.
            current_metrics=self.repo.metric_percentile(ids, names, start, end, 0.9),
        )

    # -- collectors ------------------------------------------------------------------------------------------

    def _log_evidence(self, ctx: _Context, pattern: LogPattern, corroborating: list[LogPattern]) -> list[Evidence]:
        before = ctx.baseline_counts.get((pattern.service, pattern.message), 0)
        new = ctx.is_new(pattern)
        out = [
            Evidence(
                EvidenceKind.LOG_PATTERN,
                EvidenceStance.SUPPORTS,
                f"{pattern.count} {pattern.message} events from {pattern.service} between "
                f"{pattern.first_seen:%H:%M} and {pattern.last_seen:%H:%M} UTC "
                + ("(none in the preceding 2 h)" if before == 0 else f"({before} in the preceding 2 h)"),
                0.45 if before == 0 else (0.3 if new else 0.1),
                {
                    "service": pattern.service,
                    "level": pattern.level,
                    "message": pattern.message,
                    "count": pattern.count,
                    "baseline_count": before,
                    "first_seen": pattern.first_seen.isoformat(),
                    "last_seen": pattern.last_seen.isoformat(),
                    "first_log_id": pattern.first_id,
                    "sample": pattern.sample,
                },
                ref=f"LOG-{pattern.first_id}",
            )
        ]
        for p in corroborating:
            out.append(
                Evidence(
                    EvidenceKind.LOG_PATTERN,
                    EvidenceStance.SUPPORTS,
                    f"{p.count} '{p.message}' events from {p.service} in the same window",
                    0.1,
                    {
                        "service": p.service,
                        "level": p.level,
                        "message": p.message,
                        "count": p.count,
                        "first_log_id": p.first_id,
                        "sample": p.sample,
                    },
                    ref=f"LOG-{p.first_id}",
                )  # fmt: skip
            )
        return out

    def _metric_evidence(self, ctx: _Context, emitter: str, implicated: set[str]) -> list[Evidence]:
        emitter_deps = {d.name for d in ctx.services[emitter].dependencies} if emitter in ctx.services else set()
        out: list[Evidence] = []
        healthy: dict[str, list[str]] = defaultdict(list)
        anomalous_services: set[str] = set()
        for svc in ctx.services:
            for metric, rule in METRIC_RULES.items():
                base, cur = ctx.baseline_metrics.get((svc, metric)), ctx.current_metrics.get((svc, metric))
                if base is None or cur is None or base <= 0:
                    continue
                change = (cur - base) / base * 100
                data = {"service": svc, "metric": metric, "baseline": round(base, 4), "current": round(cur, 4),
                        "change_pct": round(change, 1)}  # fmt: skip
                if not rule.anomalous(base, cur):
                    if metric != "cpu_usage":
                        healthy[svc].append(f"{rule.label} {rule.fmt(cur)} (baseline {rule.fmt(base)})")
                    continue
                ref = f"METRIC-{svc}-{METRIC_SHORT[metric]}"
                anomalous_services.add(svc)
                change_text = f"increased {change:,.0f}%" if metric.endswith("_ms") else "rose"
                summary = f"{svc} {rule.label} {change_text} ({rule.fmt(base)} → {rule.fmt(cur)})"
                if svc == emitter or svc in implicated:
                    out.append(
                        Evidence(
                            EvidenceKind.METRIC_DELTA, EvidenceStance.SUPPORTS, summary, rule.weight, data, ref=ref
                        )
                    )
                elif svc in emitter_deps:
                    # A sick dependency suggests the fault sits further upstream than this hypothesis claims.
                    out.append(
                        Evidence(
                            EvidenceKind.DEPENDENCY_HEALTH,
                            EvidenceStance.CONTRADICTS,
                            f"Dependency {summary}",
                            rule.weight,
                            data,
                            ref=ref,
                        )
                    )
                else:
                    out.append(
                        Evidence(
                            EvidenceKind.METRIC_DELTA,
                            EvidenceStance.CONTEXT,
                            f"{summary}: downstream impact",
                            0,
                            data,
                            ref=ref,
                        )
                    )
        for dep in sorted(emitter_deps & ctx.services.keys()):
            if dep not in anomalous_services and dep not in implicated and healthy.get(dep):
                out.append(
                    Evidence(
                        EvidenceKind.DEPENDENCY_HEALTH,
                        EvidenceStance.SUPPORTS,
                        f"{dep} remains healthy: " + ", ".join(healthy[dep]),
                        0.15,
                        {"service": dep, "checks": healthy[dep]},
                        ref=f"METRIC-{dep}-health",
                    )
                )
        return out

    def _change_evidence(self, ctx: _Context, related: set[str]) -> list[Evidence]:
        changes = [
            c
            for c in self.repo.logs_matching(CHANGE_MARKERS, ctx.start - CHANGE_LOOKBACK, ctx.start + BASELINE_GAP)
            if c.service in ctx.services
        ]
        if not changes:
            return [
                Evidence(
                    EvidenceKind.CHANGE_EVENT,
                    EvidenceStance.CONTEXT,
                    "No deployments, configuration changes or scheduled jobs in the 30 minutes before onset",
                    ref="CHANGE-none",
                )
            ]
        out = []
        for c in changes:
            minutes = round((ctx.start - c.ts).total_seconds() / 60)
            when = f"{minutes} min before onset" if minutes > 0 else "at onset"
            details = ", ".join(f"{k}={v}" for k, v in c.attributes.items())
            out.append(
                Evidence(
                    EvidenceKind.CHANGE_EVENT,
                    EvidenceStance.SUPPORTS if c.service in related else EvidenceStance.CONTEXT,
                    f"{c.message} on {c.service} ({details}) {when}",
                    0.15 if c.service in related else 0,
                    {"service": c.service, "message": c.message, "ts": c.ts.isoformat(), "attributes": c.attributes},
                    ref=f"CHANGE-{c.id}",
                )
            )
        return out

    def _similar_incidents(self, ctx: _Context, signature: str) -> list[Evidence]:
        sig = signature.lower()
        matches = [
            i
            for i in self.repo.resolved_incidents_with_signature(ctx.incident.id)
            if i.signature and (i.signature.lower() in sig or sig in i.signature.lower())
        ][:2]
        out = []
        for rank, inc in enumerate(matches):
            days = (ctx.start - inc.started_at).days
            cause = (inc.root_cause or inc.summary or "").split(". ")[0].rstrip(".")
            out.append(
                Evidence(
                    EvidenceKind.SIMILAR_INCIDENT,
                    EvidenceStance.SUPPORTS if rank == 0 else EvidenceStance.CONTEXT,
                    f"Similar to incident {inc.number} ({days} days ago): {cause}",
                    0.2 if rank == 0 else 0,
                    {"number": inc.number, "title": inc.title, "signature": inc.signature, "days_ago": days},
                    related_incident_id=inc.id,
                    ref=inc.number,
                )
            )
        return out

    def _documents(self, query: str, emitter: str, tags: tuple[str, ...] = ()) -> list[Evidence]:
        """Runbook passages retrieved by semantic search (RAG), cited down to the section."""
        return [
            Evidence(
                EvidenceKind.DOCUMENT,
                EvidenceStance.CONTEXT,
                f"{p.title} §{p.section} {p.heading}",
                data={
                    "slug": p.slug,
                    "category": p.category,
                    "chunk_id": p.chunk_id,
                    "section": p.section,
                    "heading": p.heading,
                    "similarity": p.similarity,
                    "score": p.score,
                    "excerpt": p.content[:700],
                },
                document_id=p.document_id,
                chunk_id=p.chunk_id,
                ref=f"{DOC_PREFIX.get(p.category, 'DOC')}-{p.slug} §{p.section}",
            )
            for p in self.retriever.search(query, k=3, service=emitter, tags=tags, per_document=1)
        ]

    # -- hypotheses ------------------------------------------------------------------------------------------

    def _evaluate(self, ctx: _Context, hyp: Hypothesis) -> InvestigationResult | None:
        hits = [p for p in ctx.patterns if hyp.signature.lower() in p.message.lower()]
        if not hits:
            return None
        pattern = max(hits, key=lambda p: p.count)
        corroborating = [p for p in ctx.patterns if any(c.lower() in p.message.lower() for c in hyp.corroborating)]
        implicated = set(hyp.implicates)
        related = {pattern.service, *implicated}
        query = build_query(
            ctx.incident.title, hyp.signature, pattern.message, *(p.message for p in corroborating), pattern.service
        )

        evidence = [
            *self._log_evidence(ctx, pattern, corroborating),
            *self._metric_evidence(ctx, pattern.service, implicated),
            *self._change_evidence(ctx, related),
            *self._similar_incidents(ctx, hyp.signature),
        ]
        values = {**pattern.sample, "service": pattern.service, "count": pattern.count,
                  "minutes": math.ceil(ctx.minutes)}  # fmt: skip
        window = {"start": ctx.start.isoformat(timespec="seconds"), "end": ctx.end.isoformat(timespec="seconds"),
                  "lookback": (ctx.start - CHANGE_LOOKBACK).isoformat(timespec="seconds")}  # fmt: skip
        queries = [QueryTemplate(q.title, q.purpose, _fill(q.sql, window)) for q in hyp.queries]
        return InvestigationResult(
            hypothesis_key=hyp.key,
            root_cause=_fill(hyp.root_cause, values),
            confidence=score(evidence),
            recommended_steps=[_fill(s, values) for s in hyp.steps],
            suggested_sql=queries[0].sql if queries else None,
            evidence=evidence,
            window_start=ctx.start,
            window_end=ctx.end,
            search_query=query,
            queries=queries,
            emitter=pattern.service,
            doc_tags=hyp.doc_tags,
        )

    def _unknown(self, ctx: _Context) -> InvestigationResult:
        new = sorted((p for p in ctx.patterns if ctx.is_new(p)), key=ctx.novelty, reverse=True)
        if not new:
            return InvestigationResult(
                hypothesis_key="unknown",
                root_cause="No anomalous log patterns were found in the investigation window.",
                confidence=0.1,
                recommended_steps=["Confirm the customer reports and widen the investigation window"],
                suggested_sql=None,
                evidence=self._change_evidence(ctx, set()),
                window_start=ctx.start,
                window_end=ctx.end,
            )
        top = new[0]
        # No known failure mode matched, so the knowledge base is searched with the strongest new signal: a runbook
        # may still describe it even though no hypothesis encodes it.
        query = build_query(ctx.incident.title, top.message, top.service)
        evidence = [
            *self._log_evidence(ctx, top, []),
            *self._metric_evidence(ctx, top.service, set()),
            *self._change_evidence(ctx, {top.service}),
            *self._similar_incidents(ctx, top.message),
        ]
        team = ctx.services[top.service].owner_team if top.service in ctx.services else "owning"
        values = {"message": top.message, "service": top.service, "team": team}
        return InvestigationResult(
            hypothesis_key="unknown",
            root_cause=(
                f"No known failure mode matched. The strongest new signal is {top.count} '{top.message}' events "
                f"from {top.service} since onset."
            ),
            confidence=min(UNKNOWN_MAX_CONFIDENCE, score(evidence)),
            recommended_steps=[_fill(s, values) for s in UNKNOWN_STEPS],
            suggested_sql=None,
            evidence=evidence,
            window_start=ctx.start,
            window_end=ctx.end,
            search_query=query,
            emitter=top.service,
        )

    def investigate(self, incident: Incident, step: Callable[[str], None] | None = None) -> InvestigationResult:
        """`step(key)` is called as each stage starts, so a background worker can report progress."""
        step = step or (lambda _key: None)
        step("signals")  # logs + metrics for the window and the baseline
        ctx = self._context(incident)
        step("hypotheses")  # changes, history, scoring
        candidates = [r for h in HYPOTHESES if (r := self._evaluate(ctx, h)) is not None]
        candidates.sort(key=lambda r: r.confidence, reverse=True)
        result = candidates[0] if candidates else self._unknown(ctx)
        result.alternatives = [(r.hypothesis_key, r.confidence) for r in candidates]
        step("documents")  # RAG, for the winning hypothesis only (documents never change the score)
        if result.search_query and result.emitter:
            result.evidence += self._documents(result.search_query, result.emitter, result.doc_tags)
        order = {EvidenceStance.SUPPORTS: 0, EvidenceStance.CONTRADICTS: 1, EvidenceStance.CONTEXT: 2}
        result.evidence.sort(key=lambda e: (e.kind == EvidenceKind.DOCUMENT, order[e.stance], -e.weight))
        seen: dict[str, int] = {}
        for e in result.evidence:  # refs must be unique within a run (e.g. two parts of one section)
            seen[e.ref] = seen.get(e.ref, 0) + 1
            if seen[e.ref] > 1:
                e.ref = f"{e.ref}#{seen[e.ref]}"
        return result

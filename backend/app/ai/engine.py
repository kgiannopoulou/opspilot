"""Rules-based investigation engine (rules-v1).

Evidence first (ADR 0004): collectors query the stored logs, metrics, incident history and knowledge base; each
finding becomes an `Evidence` item with the numbers behind it. Every known hypothesis whose signature appears in the
logs is scored only from that evidence, and the best-supported one wins:

    confidence = (1 - Π(1 - w) over supporting evidence) × Π(1 - w) over contradicting evidence

so independent pieces of support compound, and any contradiction pulls the score down. No LLM is involved; a later
engine can summarise the same evidence in prose without being allowed to add facts.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.ai.hypotheses import CHANGE_MARKERS, HYPOTHESES, UNKNOWN_STEPS, Hypothesis
from app.models import Incident, Service
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


@dataclass
class InvestigationResult:
    hypothesis_key: str
    root_cause: str
    confidence: float
    recommended_steps: list[str]
    suggested_sql: str | None
    evidence: list[Evidence]
    window_start: datetime
    window_end: datetime


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

    # -- context ---------------------------------------------------------------------------------------------

    def _context(self, incident: Incident) -> _Context:
        start = incident.started_at
        latest = self.repo.latest_signal()
        end = min(incident.resolved_at or latest or start + ONSET_WINDOW, start + ONSET_WINDOW)
        end = max(end, start + timedelta(minutes=1))

        scope: dict[str, Service] = {}
        for svc in incident.affected_services or ([incident.primary_service] if incident.primary_service else []):
            scope[svc.name] = svc
            for dep in svc.dependencies:
                scope.setdefault(dep.name, dep)
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
                f"{pattern.count} {pattern.message} events from {pattern.service} since onset "
                + ("(none in the preceding 2 h)" if before == 0 else f"({before} in the preceding 2 h)"),
                0.45 if before == 0 else (0.3 if new else 0.1),
                {
                    "service": pattern.service,
                    "level": pattern.level,
                    "message": pattern.message,
                    "count": pattern.count,
                    "baseline_count": before,
                    "first_seen": pattern.first_seen.isoformat(),
                    "sample": pattern.sample,
                },
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
                        "sample": p.sample,
                    },
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
                anomalous_services.add(svc)
                change_text = f"increased {change:,.0f}%" if metric.endswith("_ms") else "rose"
                summary = f"{svc} {rule.label} {change_text} ({rule.fmt(base)} → {rule.fmt(cur)})"
                if svc == emitter or svc in implicated:
                    out.append(Evidence(EvidenceKind.METRIC_DELTA, EvidenceStance.SUPPORTS, summary, rule.weight, data))
                elif svc in emitter_deps:
                    # A sick dependency suggests the fault sits further upstream than this hypothesis claims.
                    out.append(
                        Evidence(
                            EvidenceKind.DEPENDENCY_HEALTH,
                            EvidenceStance.CONTRADICTS,
                            f"Dependency {summary}",
                            rule.weight,
                            data,
                        )
                    )
                else:
                    out.append(
                        Evidence(
                            EvidenceKind.METRIC_DELTA, EvidenceStance.CONTEXT, f"{summary}: downstream impact", 0, data
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
                )
            )
        return out

    def _documents(self, tags: tuple[str, ...], emitter: str) -> list[Evidence]:
        wanted = {t.lower() for t in tags}

        def relevance(doc: Any) -> tuple[int, int]:
            overlap = len(wanted & {t.lower() for t in doc.tags})
            return (overlap + (1 if doc.service and doc.service.name == emitter else 0), -doc.id)

        docs = sorted(self.repo.documents_tagged(tags), key=relevance, reverse=True)[:3]
        return [
            Evidence(
                EvidenceKind.DOCUMENT,
                EvidenceStance.CONTEXT,
                d.title,
                data={"slug": d.slug, "category": d.category.value},
                document_id=d.id,
            )
            for d in docs
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

        evidence = [
            *self._log_evidence(ctx, pattern, corroborating),
            *self._metric_evidence(ctx, pattern.service, implicated),
            *self._change_evidence(ctx, related),
            *self._similar_incidents(ctx, hyp.signature),
            *self._documents(hyp.doc_tags, pattern.service),
        ]
        values = {**pattern.sample, "service": pattern.service, "count": pattern.count,
                  "minutes": math.ceil(ctx.minutes)}  # fmt: skip
        return InvestigationResult(
            hypothesis_key=hyp.key,
            root_cause=_fill(hyp.root_cause, values),
            confidence=score(evidence),
            recommended_steps=[_fill(s, values) for s in hyp.steps],
            suggested_sql=_fill(hyp.suggested_sql, values) if hyp.suggested_sql else None,
            evidence=evidence,
            window_start=ctx.start,
            window_end=ctx.end,
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
        )

    def investigate(self, incident: Incident) -> InvestigationResult:
        ctx = self._context(incident)
        candidates = [r for h in HYPOTHESES if (r := self._evaluate(ctx, h)) is not None]
        result = max(candidates, key=lambda r: r.confidence) if candidates else self._unknown(ctx)
        order = {EvidenceStance.SUPPORTS: 0, EvidenceStance.CONTRADICTS: 1, EvidenceStance.CONTEXT: 2}
        result.evidence.sort(key=lambda e: (e.kind == EvidenceKind.DOCUMENT, order[e.stance], -e.weight))
        return result

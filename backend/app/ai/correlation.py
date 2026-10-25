"""Correlate a customer ticket with what the platform was doing when it was reported.

    ticket created 14:31 ─► window 14:16–14:46 ─► the reported service, what it depends on, what depends on it
                         ─► per service: WARN/ERROR patterns vs. the 2 h before, key metrics vs. baseline
                         ─► "payment-service: 31 GatewayTimeoutException (new) · order-service: 19 Payment failed
                             · postgres-primary: normal"

The result is evidence, not a conclusion: it is shown next to the ticket, fed to the triage classifier (an
ambiguous ticket can be routed to the one service that is actually misbehaving), and used to point at an active
incident the ticket probably belongs to. Each pattern carries the same citation key as investigation evidence
(LOG-<id>), so it can be cited the same way.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.engine import BASELINE, METRIC_RULES, METRIC_SHORT
from app.models import Incident, Service, Ticket, service_dependencies
from app.models.enums import IncidentStatus
from app.repositories.evidence import EvidenceRepository

WINDOW = timedelta(minutes=15)
NEW_RATE_FACTOR = 5  # a pattern is "new" at 5× its baseline rate (or absent before), as in the investigation engine


@dataclass
class PatternSignal:
    ref: str
    level: str
    message: str
    count: int
    baseline_count: int
    new: bool
    first_seen: str
    sample: dict[str, Any]


@dataclass
class MetricSignal:
    ref: str
    metric: str
    label: str
    baseline: float
    current: float
    change_pct: float
    anomalous: bool
    display: str


@dataclass
class ServiceSignals:
    service: str
    relation: str  # "reported" | "dependency" | "dependent" | "platform"
    status: str  # "anomalous" | "normal" | "no data"
    patterns: list[PatternSignal] = field(default_factory=list)
    metrics: list[MetricSignal] = field(default_factory=list)

    @property
    def summary(self) -> str:
        new = [p for p in self.patterns if p.new]
        if new:
            return ", ".join(f"{p.count} {p.message}" for p in new[:2])
        bad = [m for m in self.metrics if m.anomalous]
        if bad:
            return ", ".join(m.display for m in bad[:2])
        return self.status


@dataclass
class Correlation:
    ticket: str
    anchor: datetime
    window_start: datetime
    window_end: datetime
    services: list[ServiceSignals]
    suspected_incident: dict[str, Any] | None = None

    @property
    def anomalous(self) -> list[ServiceSignals]:
        return [s for s in self.services if s.status == "anomalous"]

    @property
    def headline(self) -> str:
        if not self.services:
            return "No service in scope"
        return " · ".join(f"{s.service}: {s.summary}" for s in self.services)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for svc, original in zip(out["services"], self.services, strict=True):
            svc["summary"] = original.summary
        out["headline"] = self.headline
        return out


class LogCorrelator:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = EvidenceRepository(db)

    def _scope(self, service_name: str | None) -> dict[str, tuple[Service, str]]:
        services = {s.name: s for s in self.db.scalars(select(Service))}
        reported = services.get(service_name) if service_name else None
        if reported is None:  # unknown service: look at the whole platform
            return {name: (svc, "platform") for name, svc in sorted(services.items())}
        scope = {reported.name: (reported, "reported")}
        for dep in reported.dependencies:
            scope.setdefault(dep.name, (dep, "dependency"))
        dependents = self.db.scalars(
            select(Service)
            .join(service_dependencies, service_dependencies.c.service_id == Service.id)
            .where(service_dependencies.c.depends_on_id == reported.id)
        )
        for svc in dependents:
            scope.setdefault(svc.name, (svc, "dependent"))
        return scope

    def correlate(self, ticket: Ticket, service: str | None = None) -> Correlation:
        """`service` overrides the ticket's own (e.g. the classifier's suggestion while the ticket has none)."""
        anchor = ticket.created_at
        latest = self.repo.latest_signal()
        start = anchor - WINDOW
        end = anchor + WINDOW
        if latest is not None:
            end = max(min(end, latest + timedelta(seconds=1)), start + timedelta(minutes=1))
        scope = self._scope(service or (ticket.service.name if ticket.service else None))
        ids = [svc.id for svc, _ in scope.values()]
        minutes = (end - start).total_seconds() / 60
        b_start, b_end = start - BASELINE, start
        baseline = {(p.service, p.message): p.count for p in self.repo.log_patterns(ids, b_start, b_end)}
        baseline_minutes = BASELINE.total_seconds() / 60
        names = list(METRIC_RULES)
        base_metrics = self.repo.metric_percentile(ids, names, b_start, b_end, 0.5)
        cur_metrics = self.repo.metric_percentile(ids, names, start, end, 0.9)

        signals = {name: ServiceSignals(name, relation, "no data") for name, (_, relation) in scope.items()}
        for p in self.repo.log_patterns(ids, start, end):
            before = baseline.get((p.service, p.message), 0)
            new = before == 0 or p.count / minutes >= NEW_RATE_FACTOR * before / baseline_minutes
            signals[p.service].patterns.append(
                PatternSignal(
                    f"LOG-{p.first_id}", p.level, p.message, p.count, before, new, p.first_seen.isoformat(), p.sample
                )  # fmt: skip
            )
        for (svc, metric), cur in sorted(cur_metrics.items()):
            base = base_metrics.get((svc, metric))
            if base is None or base <= 0:
                continue
            rule = METRIC_RULES[metric]
            anomalous = bool(rule.anomalous(base, cur))
            signals[svc].metrics.append(
                MetricSignal(
                    ref=f"METRIC-{svc}-{METRIC_SHORT[metric]}",
                    metric=metric,
                    label=rule.label,
                    baseline=round(base, 4),
                    current=round(cur, 4),
                    change_pct=round((cur - base) / base * 100, 1),
                    anomalous=anomalous,
                    display=f"{rule.label} {rule.fmt(base)} → {rule.fmt(cur)}",
                )
            )
        for s in signals.values():
            if s.patterns or s.metrics:
                s.status = (
                    "anomalous" if any(p.new for p in s.patterns) or any(m.anomalous for m in s.metrics) else "normal"
                )

        order = {"reported": 0, "dependency": 1, "dependent": 2, "platform": 3}
        ranked = sorted(signals.values(), key=lambda s: (order[s.relation], s.status != "anomalous", s.service))
        correlation = Correlation(ticket.number, anchor, start, end, ranked)
        correlation.suspected_incident = self._suspected_incident(correlation)
        return correlation

    def _suspected_incident(self, correlation: Correlation) -> dict[str, Any] | None:
        """An open incident that started before the window closed and covers one of the misbehaving services."""
        anomalous = {s.service for s in correlation.anomalous}
        if not anomalous:
            return None
        candidates = self.db.scalars(
            select(Incident)
            .where(Incident.status != IncidentStatus.RESOLVED, Incident.started_at <= correlation.window_end)
            .order_by(Incident.started_at.desc())
        ).unique()
        for incident in candidates:
            overlap = sorted(anomalous & {s.name for s in incident.affected_services})
            if overlap:
                return {"number": incident.number, "title": incident.title, "services": overlap}
        return None

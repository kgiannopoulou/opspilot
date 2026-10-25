"""Queries the investigation engine uses to collect evidence. Read-only; every aggregation runs in PostgreSQL."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Text, cast, func, select
from sqlalchemy.orm import Session

from app.models import ApplicationLog, Incident, MetricSample, Service
from app.models.enums import IncidentStatus, LogLevel


@dataclass(frozen=True)
class LogPattern:
    service: str
    level: str
    message: str
    count: int
    first_seen: datetime
    last_seen: datetime
    sample: dict[str, Any]
    first_id: int  # id of the earliest matching line: what a citation like [LOG-48392] points at


@dataclass(frozen=True)
class LogEvent:
    id: int
    ts: datetime
    service: str
    level: str
    message: str
    attributes: dict[str, Any]


class EvidenceRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def latest_signal(self) -> datetime | None:
        """Timestamp of the newest stored log or metric: "now" as far as the evidence is concerned."""
        return max(
            (ts for ts in (self.db.scalar(select(func.max(ApplicationLog.ts))),
                           self.db.scalar(select(func.max(MetricSample.ts)))) if ts is not None),
            default=None,
        )  # fmt: skip

    def log_patterns(self, service_ids: list[int], start: datetime, end: datetime) -> list[LogPattern]:
        """WARN/ERROR log lines in [start, end) grouped by service and message (served by the partial index)."""
        rows = self.db.execute(
            select(
                Service.name,
                ApplicationLog.level,
                ApplicationLog.message,
                func.count(),
                func.min(ApplicationLog.ts),
                func.max(ApplicationLog.ts),
                func.min(cast(ApplicationLog.attributes, Text)),  # deterministic sample (smallest JSON text)
                func.min(ApplicationLog.id),
            )
            .join(Service, Service.id == ApplicationLog.service_id)
            .where(
                ApplicationLog.service_id.in_(service_ids),
                ApplicationLog.level.in_([LogLevel.WARN, LogLevel.ERROR]),
                ApplicationLog.ts >= start,
                ApplicationLog.ts < end,
            )
            .group_by(Service.name, ApplicationLog.level, ApplicationLog.message)
            .order_by(func.count().desc())
        ).all()
        return [
            LogPattern(name, str(level), message, count, first, last, json.loads(sample) if sample else {}, first_id)
            for name, level, message, count, first, last, sample, first_id in rows
        ]

    def logs_matching(self, markers: tuple[str, ...], start: datetime, end: datetime) -> list[LogEvent]:
        """Log lines whose message is one of the markers (change events: config reloads, jobs, deploys)."""
        if not markers:
            return []
        rows = self.db.execute(
            select(ApplicationLog.id, ApplicationLog.ts, Service.name, ApplicationLog.level, ApplicationLog.message,
                   ApplicationLog.attributes)
            .join(Service, Service.id == ApplicationLog.service_id)
            .where(
                ApplicationLog.ts >= start,
                ApplicationLog.ts < end,
                ApplicationLog.message.in_(markers),
            )
            .order_by(ApplicationLog.ts)
        ).all()  # fmt: skip
        return [LogEvent(id_, ts, svc, str(level), msg, attrs) for id_, ts, svc, level, msg, attrs in rows]

    def recent_problem_logs(self, service_ids: list[int], start: datetime, end: datetime, limit: int) -> list[LogEvent]:
        rows = self.db.execute(
            select(ApplicationLog.id, ApplicationLog.ts, Service.name, ApplicationLog.level, ApplicationLog.message,
                   ApplicationLog.attributes)
            .join(Service, Service.id == ApplicationLog.service_id)
            .where(
                ApplicationLog.service_id.in_(service_ids),
                ApplicationLog.level.in_([LogLevel.WARN, LogLevel.ERROR]),
                ApplicationLog.ts >= start,
                ApplicationLog.ts < end,
            )
            .order_by(ApplicationLog.ts.desc())
            .limit(limit)
        ).all()  # fmt: skip
        return [LogEvent(id_, ts, svc, str(level), msg, attrs) for id_, ts, svc, level, msg, attrs in rows]

    def metric_percentile(
        self, service_ids: list[int], names: list[str], start: datetime, end: datetime, q: float
    ) -> dict[tuple[str, str], float]:
        """q-th percentile of each (service, metric) series in [start, end); robust to ramps and single spikes."""
        rows = self.db.execute(
            select(Service.name, MetricSample.name, func.percentile_cont(q).within_group(MetricSample.value))
            .join(Service, Service.id == MetricSample.service_id)
            .where(
                MetricSample.service_id.in_(service_ids),
                MetricSample.name.in_(names),
                MetricSample.ts >= start,
                MetricSample.ts < end,
            )
            .group_by(Service.name, MetricSample.name)
        ).all()
        return {(svc, name): float(value) for svc, name, value in rows}

    def metric_series(
        self, service_ids: list[int], names: list[str], start: datetime, end: datetime
    ) -> list[tuple[str, str, datetime, float]]:
        return [
            (svc, name, ts, float(value))
            for svc, name, ts, value in self.db.execute(
                select(Service.name, MetricSample.name, MetricSample.ts, MetricSample.value)
                .join(Service, Service.id == MetricSample.service_id)
                .where(
                    MetricSample.service_id.in_(service_ids),
                    MetricSample.name.in_(names),
                    MetricSample.ts >= start,
                    MetricSample.ts < end,
                )
                .order_by(MetricSample.ts)
            ).all()
        ]

    def resolved_incidents_with_signature(self, exclude_id: int) -> list[Incident]:
        return list(
            self.db.scalars(
                select(Incident)
                .where(
                    Incident.status == IncidentStatus.RESOLVED,
                    Incident.signature.is_not(None),
                    Incident.id != exclude_id,
                )
                .order_by(Incident.started_at.desc())
            ).unique()
        )

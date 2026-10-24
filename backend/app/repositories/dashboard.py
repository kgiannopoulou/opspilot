"""Read-only aggregate queries behind the analyst dashboard. All heavy lifting happens in SQL."""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from app.models import Incident, IncidentTicket, MetricSample, Service, Ticket, incident_services
from app.models.enums import IncidentStatus, Severity, TicketStatus

OPEN_TICKET_STATUSES = [TicketStatus.OPEN, TicketStatus.TRIAGED, TicketStatus.INVESTIGATING]


class DashboardRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def active_incidents_by_severity(self) -> dict[str, int]:
        rows = self.db.execute(
            select(Incident.severity, func.count())
            .where(Incident.status != IncidentStatus.RESOLVED)
            .group_by(Incident.severity)
        ).all()
        counts = {s.value: 0 for s in Severity}
        counts.update({sev.value: n for sev, n in rows})
        return counts

    def resolved_count(self, since: datetime) -> int:
        return (
            self.db.scalar(
                select(func.count()).where(Incident.status == IncidentStatus.RESOLVED, Incident.resolved_at >= since)
            )
            or 0
        )

    def mttr_minutes(self, start: datetime, end: datetime) -> float | None:
        """Mean time to resolve for incidents resolved in [start, end)."""
        seconds = self.db.scalar(
            select(func.avg(func.extract("epoch", Incident.resolved_at - Incident.started_at))).where(
                Incident.resolved_at >= start, Incident.resolved_at < end
            )
        )
        return round(float(seconds) / 60, 1) if seconds is not None else None

    def ticket_counts(self, today_start: datetime) -> dict[str, int]:
        is_open = Ticket.status.in_(OPEN_TICKET_STATUSES)
        row = self.db.execute(
            select(
                func.count().filter(is_open),
                func.count().filter(is_open, Ticket.assignee_id.is_(None)),
                func.count().filter(Ticket.created_at >= today_start),
                func.count().filter(is_open, Ticket.priority == "P1"),
            )
        ).one()
        return {"open": row[0], "unassigned": row[1], "created_today": row[2], "open_p1": row[3]}

    def incident_trend(self, since: date) -> list[dict[str, Any]]:
        day = cast(func.timezone("UTC", Incident.started_at), Date)
        rows = self.db.execute(
            select(day, *(func.count().filter(Incident.severity == s).label(s.value) for s in Severity))
            .where(day >= since)
            .group_by(day)
        ).all()
        by_day = {r[0]: r for r in rows}
        out = []
        for offset in range((datetime.now(UTC).date() - since).days + 1):
            d = since + timedelta(days=offset)
            r = by_day.get(d)
            out.append({"date": d.isoformat()} | {s.value: (r._mapping[s.value] if r else 0) for s in Severity})
        return out

    def ticket_volume(self, since: date) -> list[dict[str, Any]]:
        day = cast(func.timezone("UTC", Ticket.created_at), Date)
        rows = dict(self.db.execute(select(day, func.count()).where(day >= since).group_by(day)).all())
        days = (datetime.now(UTC).date() - since).days + 1
        return [
            {"date": (since + timedelta(days=i)).isoformat(), "count": rows.get(since + timedelta(days=i), 0)}
            for i in range(days)
        ]

    def service_health(self, window: timedelta = timedelta(hours=6), bucket_minutes: int = 5) -> list[dict[str, Any]]:
        latest_ts = self.db.scalar(select(func.max(MetricSample.ts)))
        services = self.db.scalars(select(Service).order_by(Service.tier, Service.name)).all()
        active = dict(
            self.db.execute(
                select(incident_services.c.service_id, func.count())
                .join(Incident, Incident.id == incident_services.c.incident_id)
                .where(Incident.status != IncidentStatus.RESOLVED)
                .group_by(incident_services.c.service_id)
            ).all()
        )
        if latest_ts is None:
            return [self._health_row(s, None, None, None, None, [], active.get(s.id, 0)) for s in services]

        start = latest_ts - window
        bucket = func.date_bin(timedelta(minutes=bucket_minutes), MetricSample.ts, start)
        series_rows = self.db.execute(
            select(MetricSample.service_id, MetricSample.name, bucket.label("bucket"), func.avg(MetricSample.value))
            .where(
                MetricSample.ts > start,
                MetricSample.name.in_(["error_rate", "api_latency_p95_ms", "query_latency_p95_ms"]),
            )
            .group_by(MetricSample.service_id, MetricSample.name, bucket)
            .order_by(bucket)
        ).all()
        series: dict[tuple[int, str], list[tuple[datetime, float]]] = defaultdict(list)
        for service_id, name, ts, value in series_rows:
            series[(service_id, name)].append((ts, float(value)))

        # Baseline = median over the window; robust to the incident spike at the end of it.
        baseline_rows = self.db.execute(
            select(
                MetricSample.service_id,
                MetricSample.name,
                func.percentile_cont(0.5).within_group(MetricSample.value),
            )
            .where(MetricSample.ts > start, MetricSample.name.in_(["api_latency_p95_ms", "query_latency_p95_ms"]))
            .group_by(MetricSample.service_id, MetricSample.name)
        ).all()
        baselines = {(sid, name): float(v) for sid, name, v in baseline_rows}

        out = []
        for s in services:
            latency_metric = (
                "query_latency_p95_ms" if (s.id, "query_latency_p95_ms") in series else "api_latency_p95_ms"
            )
            err = series.get((s.id, "error_rate"), [])
            lat = series.get((s.id, latency_metric), [])
            out.append(
                self._health_row(
                    s,
                    err[-1][1] if err else None,
                    lat[-1][1] if lat else None,
                    baselines.get((s.id, latency_metric)),
                    latest_ts,
                    [{"ts": ts.isoformat(), "error_rate": round(v, 4)} for ts, v in err],
                    active.get(s.id, 0),
                )
            )
        return out

    @staticmethod
    def _health_row(
        s: Service,
        error_rate: float | None,
        latency: float | None,
        baseline: float | None,
        as_of: datetime | None,
        error_series: list[dict[str, Any]],
        active_incidents: int,
    ) -> dict[str, Any]:
        ratio = (latency / baseline) if latency and baseline else 1.0
        errors = error_rate or 0.0  # the database reports no error rate; judge it on latency alone
        if latency is None and error_rate is None:
            status = "unknown"
        elif errors >= 0.15 or ratio >= 4:
            status = "critical"
        elif errors >= 0.03 or ratio >= 2:
            status = "degraded"
        else:
            status = "healthy"
        return {
            "name": s.name,
            "display_name": s.display_name,
            "tier": s.tier,
            "owner_team": s.owner_team,
            "status": status,
            "error_rate": round(error_rate, 4) if error_rate is not None else None,
            "latency_p95_ms": round(latency) if latency is not None else None,
            "latency_baseline_ms": round(baseline) if baseline is not None else None,
            "as_of": as_of.isoformat() if as_of else None,
            "active_incidents": active_incidents,
            "error_rate_series": error_series,
        }

    def ticket_count_per_incident(self) -> dict[int, int]:
        return dict(
            self.db.execute(select(IncidentTicket.incident_id, func.count()).group_by(IncidentTicket.incident_id)).all()
        )

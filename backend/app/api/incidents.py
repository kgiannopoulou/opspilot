from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.core.database import get_db
from app.models import Incident, User
from app.models.enums import IncidentStatus, Severity
from app.repositories.dashboard import DashboardRepository
from app.repositories.evidence import EvidenceRepository
from app.schemas.common import Page
from app.schemas.incident import (
    IncidentDetail,
    IncidentStatusChange,
    IncidentSummary,
    IncidentTelemetry,
    InvestigationOut,
    LinkedTicket,
    LogLine,
    MetricPoint,
    MetricSeries,
)
from app.services.incidents import IncidentService, allowed_incident_transitions
from app.services.investigations import InvestigationService

router = APIRouter(prefix="/incidents", tags=["incidents"])

DB = Annotated[Session, Depends(get_db)]
Reader = Annotated[User, Depends(require(Permission.INCIDENTS_READ))]
Investigator = Annotated[User, Depends(require(Permission.INCIDENTS_INVESTIGATE))]

TELEMETRY_METRICS = ["api_latency_p95_ms", "error_rate", "query_latency_p95_ms", "database_connections"]
TELEMETRY_LEAD = timedelta(minutes=60)  # show the hour before onset so the change is visible


@router.get("", response_model=Page[IncidentSummary])
def list_incidents(
    db: DB,
    _: Reader,
    state: Literal["active", "resolved", "all"] = "all",
    severity: list[Severity] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> Page[IncidentSummary]:
    stmt = select(Incident)
    if state == "active":
        stmt = stmt.where(Incident.status != IncidentStatus.RESOLVED)
    elif state == "resolved":
        stmt = stmt.where(Incident.status == IncidentStatus.RESOLVED)
    if severity:
        stmt = stmt.where(Incident.severity.in_(severity))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    # Active incidents first, then most recent.
    rows = db.scalars(
        stmt.order_by((Incident.status == IncidentStatus.RESOLVED).asc(), Incident.started_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).unique()
    counts = DashboardRepository(db).ticket_count_per_incident()
    now = datetime.now(UTC)
    return Page(
        items=[IncidentSummary.build(i, counts.get(i.id, 0), now) for i in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


def to_detail(db: Session, incident: Incident) -> IncidentDetail:
    summary = IncidentSummary.build(incident, len(incident.tickets), datetime.now(UTC))
    latest = InvestigationService(db).latest(incident)
    return IncidentDetail(
        **summary.model_dump(),
        summary=incident.summary,
        root_cause=incident.root_cause,
        signature=incident.signature,
        detected_at=incident.detected_at,
        tickets=[LinkedTicket.model_validate(t) for t in sorted(incident.tickets, key=lambda t: t.created_at)],
        allowed_transitions=allowed_incident_transitions(incident.status),
        latest_investigation=InvestigationOut.model_validate(latest) if latest else None,
    )


@router.get("/{number}", response_model=IncidentDetail)
def get_incident(number: str, db: DB, _: Reader) -> IncidentDetail:
    return to_detail(db, IncidentService(db).get(number))


@router.post("/{number}/status", response_model=IncidentDetail)
def change_incident_status(number: str, data: IncidentStatusChange, db: DB, user: Investigator) -> IncidentDetail:
    incident = IncidentService(db).change_status(number, data.status, user, data.root_cause)
    return to_detail(db, incident)


@router.get("/{number}/investigations", response_model=list[InvestigationOut])
def list_investigations(number: str, db: DB, _: Reader) -> list[InvestigationOut]:
    incident = IncidentService(db).get(number)
    return [InvestigationOut.model_validate(i) for i in InvestigationService(db).history(incident)]


@router.post("/{number}/investigations", response_model=InvestigationOut, status_code=status.HTTP_201_CREATED)
def run_investigation(number: str, db: DB, user: Investigator) -> InvestigationOut:
    """Collect evidence from logs, metrics, past incidents and runbooks, then score known failure modes."""
    incident = IncidentService(db).get(number)
    return InvestigationOut.model_validate(InvestigationService(db).run(incident, user))


def _window(db: Session, incident: Incident) -> tuple[datetime, datetime]:
    latest = EvidenceRepository(db).latest_signal() or datetime.now(UTC)
    end = min(latest, incident.resolved_at + timedelta(minutes=30)) if incident.resolved_at else latest
    return incident.started_at - TELEMETRY_LEAD, max(end, incident.started_at + timedelta(minutes=1))


@router.get("/{number}/telemetry", response_model=IncidentTelemetry)
def incident_telemetry(number: str, db: DB, _: Reader) -> IncidentTelemetry:
    """Per-minute metric series of the affected services around the incident, for the incident page charts."""
    incident = IncidentService(db).get(number)
    start, end = _window(db, incident)
    rows = EvidenceRepository(db).metric_series(
        [s.id for s in incident.affected_services], TELEMETRY_METRICS, start, end
    )
    grouped: dict[tuple[str, str], list[MetricPoint]] = {}
    for svc, metric, ts, value in rows:
        grouped.setdefault((svc, metric), []).append(MetricPoint(ts=ts, value=value))
    return IncidentTelemetry(
        window_start=start,
        window_end=end,
        incident_start=incident.started_at,
        series=[MetricSeries(service=s, metric=m, points=p) for (s, m), p in sorted(grouped.items())],
    )


@router.get("/{number}/logs", response_model=list[LogLine])
def incident_logs(number: str, db: DB, _: Reader, limit: int = Query(default=50, ge=1, le=200)) -> list[LogLine]:
    """Most recent WARN/ERROR lines from the affected services since the incident started."""
    incident = IncidentService(db).get(number)
    _, end = _window(db, incident)
    ids = [s.id for s in incident.affected_services]
    events = EvidenceRepository(db).recent_problem_logs(ids, incident.started_at, end + timedelta(seconds=1), limit)
    return [
        LogLine(ts=e.ts, service=e.service, level=e.level, message=e.message, attributes=e.attributes) for e in events
    ]

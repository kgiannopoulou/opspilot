from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models import Incident
from app.models.enums import (
    DocumentCategory,
    EvidenceKind,
    EvidenceStance,
    IncidentStatus,
    InvestigationStatus,
    Priority,
    Severity,
    TicketStatus,
)
from app.schemas.common import ORMModel, ServiceRef, UserRef


class IncidentSummary(ORMModel):
    id: int
    number: str
    title: str
    severity: Severity
    status: IncidentStatus
    primary_service: ServiceRef | None
    affected_services: list[ServiceRef]
    commander: UserRef | None
    started_at: datetime
    resolved_at: datetime | None
    duration_minutes: int | None
    ticket_count: int = 0

    @classmethod
    def build(cls, incident: Incident, ticket_count: int = 0, now: datetime | None = None) -> "IncidentSummary":
        end = incident.resolved_at or now
        duration = int((end - incident.started_at).total_seconds() // 60) if end else None
        return cls.model_validate(
            {
                **{k: getattr(incident, k) for k in ("id", "number", "title", "severity", "status", "started_at")},
                "primary_service": incident.primary_service,
                "affected_services": incident.affected_services,
                "commander": incident.commander,
                "resolved_at": incident.resolved_at,
                "duration_minutes": duration,
                "ticket_count": ticket_count,
            }
        )


class IncidentRef(ORMModel):
    id: int
    number: str
    title: str


class DocumentRef(ORMModel):
    id: int
    slug: str
    title: str
    category: DocumentCategory


class EvidenceOut(ORMModel):
    id: int
    position: int
    kind: EvidenceKind
    stance: EvidenceStance
    summary: str
    weight: float
    data: dict[str, Any]
    related_incident: IncidentRef | None = None
    document: DocumentRef | None = None


class InvestigationOut(ORMModel):
    id: int
    status: InvestigationStatus
    engine: str
    hypothesis_key: str | None
    root_cause: str | None
    confidence: float | None
    recommended_steps: list[str]
    suggested_sql: str | None
    window_start: datetime
    window_end: datetime
    error: str | None
    duration_ms: int | None
    created_at: datetime
    completed_at: datetime | None
    requested_by: UserRef | None
    evidence: list[EvidenceOut]


class LinkedTicket(ORMModel):
    number: str
    title: str
    priority: Priority
    status: TicketStatus
    assignee: UserRef | None
    created_at: datetime


class IncidentDetail(IncidentSummary):
    summary: str
    root_cause: str | None
    signature: str | None
    detected_at: datetime | None
    tickets: list[LinkedTicket] = []
    allowed_transitions: list[IncidentStatus] = []
    latest_investigation: InvestigationOut | None = None


class IncidentStatusChange(BaseModel):
    status: IncidentStatus
    root_cause: str | None = Field(default=None, max_length=5_000, description="Required to resolve")


class MetricPoint(BaseModel):
    ts: datetime
    value: float


class MetricSeries(BaseModel):
    service: str
    metric: str
    points: list[MetricPoint]


class IncidentTelemetry(BaseModel):
    window_start: datetime
    window_end: datetime
    incident_start: datetime
    series: list[MetricSeries]


class LogLine(BaseModel):
    ts: datetime
    service: str
    level: str
    message: str
    attributes: dict[str, Any]

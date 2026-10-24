from datetime import datetime

from app.models import Incident
from app.models.enums import IncidentStatus, Severity
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

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.models import Incident, IncidentTicket, Service, TicketEvent, User
from app.models.enums import IncidentStatus, Severity, TicketEventType
from app.repositories.tickets import TicketRepository
from app.services import audit


class IncidentService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, number: str) -> Incident:
        incident = self.db.scalar(select(Incident).where(Incident.number == number))
        if incident is None:
            raise NotFoundError(f"incident {number} not found")
        return incident

    def _next_number(self) -> str:
        return f"INC-{self.db.scalar(select(func.nextval('incident_number_seq')))}"

    def declare(
        self,
        *,
        title: str,
        severity: Severity,
        primary_service: str,
        started_at: datetime,
        actor: User | None,
        summary: str = "",
        affected_services: list[str] | None = None,
        signature: str | None = None,
        commander: User | None = None,
        ticket_numbers: list[str] | None = None,
        commit: bool = True,
    ) -> Incident:
        """Create an incident, its affected services and ticket links atomically.

        If any ticket is unknown or already linked elsewhere, nothing is written (single transaction).
        """
        names = {primary_service, *(affected_services or [])}
        services = {s.name: s for s in self.db.scalars(select(Service).where(Service.name.in_(names)))}
        if missing := names - services.keys():
            raise ValidationFailed(f"unknown services: {sorted(missing)}")

        incident = Incident(
            number=self._next_number(),
            title=title,
            summary=summary,
            severity=severity,
            status=IncidentStatus.INVESTIGATING,
            primary_service_id=services[primary_service].id,
            commander_id=commander.id if commander else None,
            signature=signature,
            started_at=started_at,
            detected_at=started_at,
        )
        incident.affected_services = sorted(services.values(), key=lambda s: s.name)
        self.db.add(incident)
        self.db.flush()
        for number in ticket_numbers or []:
            self.link_ticket(incident, number, actor, commit=False)
        audit.record(self.db, actor, "incident.declared", "incident", incident.number, {"severity": severity.value})
        if commit:
            self.db.commit()
        return incident

    def link_ticket(self, incident: Incident, ticket_number: str, actor: User | None, commit: bool = True) -> None:
        ticket = TicketRepository(self.db).get_by_number(ticket_number)
        if ticket is None:
            raise ValidationFailed(f"ticket {ticket_number} not found")
        existing = self.db.scalar(select(IncidentTicket).where(IncidentTicket.ticket_id == ticket.id))
        if existing is not None:
            raise ConflictError(f"ticket {ticket_number} is already linked to an incident")
        self.db.add(
            IncidentTicket(incident_id=incident.id, ticket_id=ticket.id, linked_by_id=actor.id if actor else None)
        )
        self.db.add(
            TicketEvent(
                ticket_id=ticket.id,
                actor_id=actor.id if actor else None,
                type=TicketEventType.LINKED_TO_INCIDENT,
                to_value=incident.number,
            )
        )
        audit.record(self.db, actor, "incident.ticket_linked", "incident", incident.number, {"ticket": ticket_number})
        if commit:
            self.db.commit()

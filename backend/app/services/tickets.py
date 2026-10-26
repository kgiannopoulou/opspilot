from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationFailed
from app.models import Ticket, TicketComment, TicketEvent, User
from app.models.enums import TicketEventType, TicketStatus
from app.repositories.catalog import ServiceRepository, UserRepository
from app.repositories.tickets import TicketRepository
from app.schemas.ticket import CommentCreate, TicketCreate, TicketTransition, TicketUpdate
from app.services import audit
from app.services.ticket_workflow import REQUIRES_RESOLUTION, can_transition


class TicketService:
    """Ticket use cases. Each public method is one transaction: it either commits fully or not at all."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.tickets = TicketRepository(db)
        self.services = ServiceRepository(db)
        self.users = UserRepository(db)

    # -- helpers -------------------------------------------------------------------------------------------------

    def get(self, number: str, *, for_update: bool = False) -> Ticket:
        ticket = self.tickets.get_by_number(number, for_update=for_update)
        if ticket is None:
            raise NotFoundError(f"ticket {number} not found")
        return ticket

    def _service_id(self, name: str | None) -> int | None:
        if name is None:
            return None
        service = self.services.get_by_name(name)
        if service is None:
            raise ValidationFailed(f"unknown service {name!r}")
        return service.id

    def _event(self, ticket: Ticket, actor: User | None, type_: TicketEventType, **kwargs: object) -> None:
        """Timeline entry for the UI plus an audit row, both inside the current transaction (actor None = system)."""
        self.tickets.add_event(
            TicketEvent(ticket_id=ticket.id, actor_id=actor.id if actor else None, type=type_, **kwargs)
        )
        changes = {k: v for k, v in kwargs.items() if v not in (None, {})}
        audit.record(self.db, actor, f"ticket.{type_.value.lower()}", "ticket", ticket.number, changes)

    def _commit(self, ticket: Ticket) -> Ticket:
        self.db.commit()
        self.db.refresh(ticket)
        return ticket

    # -- use cases -----------------------------------------------------------------------------------------------

    def create(self, data: TicketCreate, actor: User) -> Ticket:
        ticket = self.tickets.add(
            Ticket(
                number=self.tickets.next_number(),
                title=data.title.strip(),
                description=data.description.strip(),
                priority=data.priority,
                source=data.source,
                labels=sorted(set(data.labels)),
                service_id=self._service_id(data.service),
                reporter_id=actor.id,
            )
        )
        self._event(ticket, actor, TicketEventType.CREATED, to_value=TicketStatus.OPEN.value)
        return self._commit(ticket)

    def update(self, number: str, data: TicketUpdate, actor: User) -> Ticket:
        ticket = self.get(number, for_update=True)
        changes = data.model_dump(exclude_unset=True)
        if "priority" in changes and changes["priority"] != ticket.priority:
            self._event(
                ticket,
                actor,
                TicketEventType.PRIORITY_CHANGED,
                from_value=ticket.priority.value,
                to_value=changes["priority"].value,
            )
            ticket.priority = changes["priority"]
        changed_fields: list[str] = []
        details: dict[str, object] = {}
        for field in ("title", "description"):
            if changes.get(field) is not None and changes[field] != getattr(ticket, field):
                setattr(ticket, field, changes[field].strip())
                changed_fields.append(field)
        if "labels" in changes and changes["labels"] is not None:
            ticket.labels = sorted(set(changes["labels"]))
            changed_fields.append("labels")
        if "service" in changes and self._service_id(changes["service"]) != ticket.service_id:
            details["service"] = {"from": ticket.service.name if ticket.service else None, "to": changes["service"]}
            ticket.service_id = self._service_id(changes["service"])
            changed_fields.append("service")
        if changed_fields:
            self._event(ticket, actor, TicketEventType.UPDATED, data={"fields": changed_fields, **details})
        return self._commit(ticket)

    def transition(self, number: str, data: TicketTransition, actor: User) -> Ticket:
        ticket = self.get(number, for_update=True)
        current, target = ticket.status, data.status
        if not can_transition(current, target):
            raise ValidationFailed(f"cannot move ticket from {current} to {target}")
        if (current, target) in REQUIRES_RESOLUTION and not (data.resolution or "").strip():
            raise ValidationFailed(f"a resolution note is required to move from {current} to {target}")

        now = datetime.now(UTC)
        ticket.status = target
        if target == TicketStatus.TRIAGED and ticket.triaged_at is None:
            ticket.triaged_at = now
        if target == TicketStatus.RESOLVED:
            ticket.resolved_at = now
        if target == TicketStatus.CLOSED:
            ticket.closed_at = now
        if target == TicketStatus.INVESTIGATING and current == TicketStatus.RESOLVED:
            ticket.resolved_at = None  # reopened
        if data.resolution:
            ticket.resolution = data.resolution.strip()

        self._event(
            ticket,
            actor,
            TicketEventType.STATUS_CHANGED,
            from_value=current.value,
            to_value=target.value,
            data={"resolution": data.resolution} if data.resolution else {},
        )
        return self._commit(ticket)

    def assign(self, number: str, assignee_id: int | None, actor: User) -> Ticket:
        ticket = self.get(number, for_update=True)
        assignee = None
        if assignee_id is not None:
            assignee = self.users.get(assignee_id)
            if assignee is None or not assignee.is_active:
                raise ValidationFailed(f"user {assignee_id} does not exist or is inactive")
        previous = ticket.assignee
        if (previous.id if previous else None) == assignee_id:
            return ticket
        ticket.assignee_id = assignee_id
        self._event(
            ticket,
            actor,
            TicketEventType.ASSIGNED,
            from_value=previous.full_name if previous else None,
            to_value=assignee.full_name if assignee else None,
        )
        return self._commit(ticket)

    def comment(self, number: str, data: CommentCreate, actor: User) -> TicketComment:
        ticket = self.get(number)
        comment = self.tickets.add_comment(
            TicketComment(ticket_id=ticket.id, author_id=actor.id, body=data.body.strip(), internal=data.internal)
        )
        self._event(ticket, actor, TicketEventType.COMMENTED, data={"comment_id": comment.id})
        self.db.commit()
        self.db.refresh(comment)
        return comment

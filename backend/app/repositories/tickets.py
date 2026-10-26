from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.models import Service, Ticket, TicketComment, TicketEvent
from app.models.enums import Priority, TicketStatus


@dataclass
class TicketFilters:
    status: list[TicketStatus] | None = None
    priority: list[Priority] | None = None
    service: str | None = None
    assignee_id: int | None = None
    unassigned: bool = False
    q: str | None = None


class TicketRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def next_number(self) -> str:
        seq = self.db.scalar(select(func.nextval("ticket_number_seq")))
        return f"TCK-{datetime.now(UTC).year}-{seq:05d}"

    def get_by_number(self, number: str, *, for_update: bool = False) -> Ticket | None:
        stmt = select(Ticket).where(Ticket.number == number)
        if for_update:
            # Serialise concurrent transitions/assignments on the same ticket.
            stmt = stmt.with_for_update(of=Ticket)
        return self.db.scalar(stmt)

    def _filtered(self, filters: TicketFilters) -> Select[Ticket]:
        stmt = select(Ticket)
        if filters.status:
            stmt = stmt.where(Ticket.status.in_(filters.status))
        if filters.priority:
            stmt = stmt.where(Ticket.priority.in_(filters.priority))
        if filters.service:
            stmt = stmt.join(Service, Ticket.service_id == Service.id).where(Service.name == filters.service)
        if filters.unassigned:
            stmt = stmt.where(Ticket.assignee_id.is_(None))
        elif filters.assignee_id is not None:
            stmt = stmt.where(Ticket.assignee_id == filters.assignee_id)
        if filters.q:
            pattern = f"%{filters.q}%"
            stmt = stmt.where(
                or_(Ticket.title.ilike(pattern), Ticket.description.ilike(pattern), Ticket.number.ilike(pattern))
            )
        return stmt

    def search(self, filters: TicketFilters, page: int, page_size: int) -> tuple[Sequence[Ticket], int]:
        stmt = self._filtered(filters)
        total = self.db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
        items = self.db.scalars(
            stmt.order_by(Ticket.priority, Ticket.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        ).all()
        return items, total

    def add(self, ticket: Ticket) -> Ticket:
        self.db.add(ticket)
        self.db.flush()
        return ticket

    def add_event(self, event: TicketEvent) -> None:
        self.db.add(event)

    def add_comment(self, comment: TicketComment) -> TicketComment:
        self.db.add(comment)
        self.db.flush()
        return comment

    def comments(self, ticket_id: int, include_internal: bool = True) -> Sequence[TicketComment]:
        stmt = select(TicketComment).where(TicketComment.ticket_id == ticket_id)
        if not include_internal:
            stmt = stmt.where(TicketComment.internal.is_(False))
        return self.db.scalars(stmt.order_by(TicketComment.created_at, TicketComment.id)).all()

    def events(self, ticket_id: int) -> Sequence[TicketEvent]:
        return self.db.scalars(
            select(TicketEvent)
            .where(TicketEvent.ticket_id == ticket_id)
            .order_by(TicketEvent.created_at, TicketEvent.id)
        ).all()

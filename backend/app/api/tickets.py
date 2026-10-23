from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.core.database import get_db
from app.models import Ticket, User
from app.models.enums import Priority, TicketStatus
from app.repositories.tickets import TicketFilters, TicketRepository
from app.schemas.common import Page
from app.schemas.ticket import (
    CommentCreate,
    CommentOut,
    TicketAssign,
    TicketCreate,
    TicketEventOut,
    TicketOut,
    TicketTransition,
    TicketUpdate,
)
from app.services.ticket_workflow import allowed_transitions
from app.services.tickets import TicketService

router = APIRouter(prefix="/tickets", tags=["tickets"])

DB = Annotated[Session, Depends(get_db)]
Reader = Annotated[User, Depends(require(Permission.TICKETS_READ))]
Writer = Annotated[User, Depends(require(Permission.TICKETS_WRITE))]


def to_out(ticket: Ticket) -> TicketOut:
    out = TicketOut.model_validate(ticket)
    out.allowed_transitions = allowed_transitions(ticket.status)
    return out


@router.get("", response_model=Page[TicketOut])
def list_tickets(
    db: DB,
    _: Reader,
    status_: list[TicketStatus] | None = Query(default=None, alias="status"),
    priority: list[Priority] | None = Query(default=None),
    service: str | None = None,
    assignee_id: int | None = None,
    unassigned: bool = False,
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> Page[TicketOut]:
    filters = TicketFilters(status_, priority, service, assignee_id, unassigned, q)
    items, total = TicketRepository(db).search(filters, page, page_size)
    return Page(items=[to_out(t) for t in items], total=total, page=page, page_size=page_size)


@router.post("", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
def create_ticket(data: TicketCreate, db: DB, user: Writer) -> TicketOut:
    return to_out(TicketService(db).create(data, user))


@router.get("/{number}", response_model=TicketOut)
def get_ticket(number: str, db: DB, _: Reader) -> TicketOut:
    return to_out(TicketService(db).get(number))


@router.patch("/{number}", response_model=TicketOut)
def update_ticket(number: str, data: TicketUpdate, db: DB, user: Writer) -> TicketOut:
    return to_out(TicketService(db).update(number, data, user))


@router.post("/{number}/transition", response_model=TicketOut)
def transition_ticket(number: str, data: TicketTransition, db: DB, user: Writer) -> TicketOut:
    return to_out(TicketService(db).transition(number, data, user))


@router.post("/{number}/assign", response_model=TicketOut)
def assign_ticket(number: str, data: TicketAssign, db: DB, user: Writer) -> TicketOut:
    return to_out(TicketService(db).assign(number, data.assignee_id, user))


@router.get("/{number}/comments", response_model=list[CommentOut])
def list_comments(number: str, db: DB, _: Reader) -> list[CommentOut]:
    ticket = TicketService(db).get(number)
    return [CommentOut.model_validate(c) for c in TicketRepository(db).comments(ticket.id)]


@router.post("/{number}/comments", response_model=CommentOut, status_code=status.HTTP_201_CREATED)
def add_comment(number: str, data: CommentCreate, db: DB, user: Writer) -> CommentOut:
    return CommentOut.model_validate(TicketService(db).comment(number, data, user))


@router.get("/{number}/events", response_model=list[TicketEventOut])
def list_events(number: str, db: DB, _: Reader) -> list[TicketEventOut]:
    ticket = TicketService(db).get(number)
    return [TicketEventOut.model_validate(e) for e in TicketRepository(db).events(ticket.id)]

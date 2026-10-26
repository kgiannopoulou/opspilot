from typing import Annotated

import redis
from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.ai.correlation import LogCorrelator
from app.auth.dependencies import ai_rate_limit, require
from app.auth.permissions import Permission
from app.auth.rate_limit import get_redis
from app.core.database import get_db
from app.models import Ticket, User
from app.models.enums import Priority, TicketStatus
from app.repositories.tickets import TicketFilters, TicketRepository
from app.schemas.audit import ActivityItem
from app.schemas.classification import ClassificationMetrics, ClassificationOut, ClassificationReview
from app.schemas.common import Page
from app.schemas.correlation import CorrelationOut
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
from app.services.audit import AuditService
from app.services.classification import ClassificationService
from app.services.ticket_workflow import allowed_transitions
from app.services.tickets import TicketService
from app.workers.jobs import JobService

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


@router.post(
    "",
    response_model=TicketOut,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Idempotent replay: the ticket created earlier with this key"},
               409: {"description": "Duplicate of an open ticket (`existing` holds its number)"}},
)  # fmt: skip
def create_ticket(
    data: TicketCreate,
    db: DB,
    user: Writer,
    background: BackgroundTasks,
    client: Annotated[redis.Redis, Depends(get_redis)],
    response: Response,
    idempotency_key: Annotated[str | None, Header(max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")] = None,
    allow_duplicate: bool = False,
) -> TicketOut:
    """Responds immediately; AI triage (correlation + classification) runs in a background worker.

    Send an `Idempotency-Key` header to make retries safe: the same key returns the original ticket (200) and
    triage is not queued twice.
    """
    ticket, created = TicketService(db).submit(data, user, idempotency_key, allow_duplicate)
    if created:
        JobService(db, client).submit("ticket.triage", "ticket", ticket.number, {"ticket_id": ticket.id}, background)
    else:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    return to_out(ticket)


@router.get("/classifications/metrics", response_model=ClassificationMetrics)
def classification_metrics(db: DB, _: Reader) -> ClassificationMetrics:
    """Acceptance rate of AI triage suggestions and which fields analysts correct most."""
    return ClassificationMetrics(**ClassificationService(db).metrics())


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


@router.get("/{number}/classification", response_model=ClassificationOut | None)
def get_classification(number: str, db: DB, _: Reader) -> ClassificationOut | None:
    latest = ClassificationService(db).latest(TicketService(db).get(number))
    return ClassificationOut.model_validate(latest) if latest else None


@router.post(
    "/{number}/classification",
    response_model=ClassificationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(ai_rate_limit)],
)
def classify_ticket(number: str, db: DB, user: Writer) -> ClassificationOut:
    """Ask for a fresh AI suggestion (supersedes one that is still pending)."""
    return ClassificationOut.model_validate(ClassificationService(db).classify(TicketService(db).get(number), user))


@router.post("/{number}/classification/{classification_id}/review", response_model=ClassificationOut)
def review_classification(
    number: str, classification_id: int, data: ClassificationReview, db: DB, user: Writer
) -> ClassificationOut:
    """Accept, modify or reject a suggestion. Only accepted/modified values are applied to the ticket."""
    service = ClassificationService(db)
    row = service.review(
        TicketService(db).get(number, for_update=True), classification_id, data.action, user,
        category=data.category, priority=data.priority, service=data.service, note=data.note,
    )  # fmt: skip
    return ClassificationOut.model_validate(row)


@router.get("/{number}/correlation", response_model=CorrelationOut)
def correlate_ticket(number: str, db: DB, _: Reader) -> CorrelationOut:
    """Logs and metrics of the ticket's service, its dependencies and dependents, ±15 minutes around creation.

    Without a service on the ticket, the pending AI suggestion's service is used, else the whole platform.
    """
    ticket = TicketService(db).get(number)
    suggestion = ClassificationService(db).latest(ticket)
    service = None if ticket.service else (suggestion.service.name if suggestion and suggestion.service else None)
    return CorrelationOut(**LogCorrelator(db).correlate(ticket, service).as_dict())


@router.get("/{number}/activity", response_model=list[ActivityItem])
def ticket_activity(number: str, db: DB, _: Reader) -> list[ActivityItem]:
    """The ticket's audit trail (including AI actions by the worker), oldest first."""
    ticket = TicketService(db).get(number)
    return [ActivityItem.build(e) for e in AuditService(db).entity_activity("ticket", ticket.number)]


@router.get("/{number}/events", response_model=list[TicketEventOut])
def list_events(number: str, db: DB, _: Reader) -> list[TicketEventOut]:
    ticket = TicketService(db).get(number)
    return [TicketEventOut.model_validate(e) for e in TicketRepository(db).events(ticket.id)]

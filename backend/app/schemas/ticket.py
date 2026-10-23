from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field

from app.models.enums import Priority, TicketEventType, TicketSource, TicketStatus
from app.schemas.common import ORMModel, ServiceRef, UserRef

Label = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[a-z0-9][a-z0-9_-]*$")]


class TicketCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=1, max_length=10_000)
    priority: Priority = Priority.P3
    service: str | None = Field(default=None, description="Service name, e.g. payment-service")
    source: TicketSource = TicketSource.CUSTOMER_SUPPORT
    labels: list[Label] = Field(default_factory=list, max_length=10)


class TicketUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=10_000)
    priority: Priority | None = None
    service: str | None = None
    labels: list[Label] | None = Field(default=None, max_length=10)


class TicketTransition(BaseModel):
    status: TicketStatus
    resolution: str | None = Field(default=None, max_length=5_000, description="Required when resolving")


class TicketAssign(BaseModel):
    assignee_id: int | None = Field(description="null un-assigns the ticket")


class TicketOut(ORMModel):
    id: int
    number: str
    title: str
    description: str
    priority: Priority
    status: TicketStatus
    source: TicketSource
    labels: list[str]
    resolution: str | None
    service: ServiceRef | None
    reporter: UserRef
    assignee: UserRef | None
    created_at: datetime
    updated_at: datetime
    triaged_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None
    allowed_transitions: list[TicketStatus] = []


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    internal: bool = False


class CommentOut(ORMModel):
    id: int
    body: str
    internal: bool
    author: UserRef
    created_at: datetime


class TicketEventOut(ORMModel):
    id: int
    type: TicketEventType
    from_value: str | None
    to_value: str | None
    data: dict[str, Any]
    actor: UserRef | None
    created_at: datetime

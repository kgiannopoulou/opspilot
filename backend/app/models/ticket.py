from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, false, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import Priority, TicketCategory, TicketEventType, TicketSource, TicketStatus, db_enum
from app.models.service import Service
from app.models.user import User


class Ticket(TimestampMixin, Base):
    __tablename__ = "tickets"
    __table_args__ = (
        # The queue view filters by status and sorts by priority/recency.
        Index("ix_tickets_status_priority_created", "status", "priority", text("created_at DESC")),
        Index("ix_tickets_service_id", "service_id"),
        Index("ix_tickets_assignee_id", "assignee_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[Priority] = mapped_column(db_enum(Priority, "priority"))
    status: Mapped[TicketStatus] = mapped_column(
        db_enum(TicketStatus, "ticket_status"), server_default=TicketStatus.OPEN.value
    )
    source: Mapped[TicketSource] = mapped_column(
        db_enum(TicketSource, "ticket_source"), server_default=TicketSource.CUSTOMER_SUPPORT.value
    )
    labels: Mapped[list[str]] = mapped_column(ARRAY(String(40)), server_default="{}")
    # Set by triage (an analyst, or an AI suggestion an analyst accepted) — never by the model alone.
    category: Mapped[TicketCategory | None] = mapped_column(db_enum(TicketCategory, "ticket_category"))
    resolution: Mapped[str | None] = mapped_column(Text)

    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id", ondelete="SET NULL"))
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    triaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    service: Mapped[Service | None] = relationship(lazy="joined")
    reporter: Mapped[User] = relationship(foreign_keys=[reporter_id], lazy="joined")
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id], lazy="joined")


class TicketComment(TimestampMixin, Base):
    __tablename__ = "ticket_comments"
    __table_args__ = (Index("ix_ticket_comments_ticket_id_created", "ticket_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    body: Mapped[str] = mapped_column(Text)
    # Internal notes are visible to staff only, never to the customer-facing channel.
    internal: Mapped[bool] = mapped_column(server_default=false())

    author: Mapped[User] = relationship(lazy="joined")


class TicketEvent(Base):
    """Append-only history of a ticket — the timeline shown in the UI."""

    __tablename__ = "ticket_events"
    __table_args__ = (Index("ix_ticket_events_ticket_id_created", "ticket_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    type: Mapped[TicketEventType] = mapped_column(db_enum(TicketEventType, "ticket_event_type"))
    from_value: Mapped[str | None] = mapped_column(String(120))
    to_value: Mapped[str | None] = mapped_column(String(120))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    actor: Mapped[User | None] = relationship(lazy="joined")

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, String, Table, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import IncidentStatus, Severity, db_enum
from app.models.service import Service
from app.models.ticket import Ticket
from app.models.user import User

incident_services = Table(
    "incident_services",
    Base.metadata,
    Column("incident_id", ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True),
    Column("service_id", ForeignKey("services.id", ondelete="CASCADE"), primary_key=True),
)


class IncidentTicket(Base):
    """Links customer tickets to the incident that explains them. A ticket belongs to at most one incident."""

    __tablename__ = "incident_tickets"

    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True, unique=True)
    linked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Incident(TimestampMixin, Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_status_severity", "status", "severity"),
        Index("ix_incidents_started_at", "started_at"),
        Index("ix_incidents_primary_service_id", "primary_service_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[Severity] = mapped_column(db_enum(Severity, "severity"))
    status: Mapped[IncidentStatus] = mapped_column(
        db_enum(IncidentStatus, "incident_status"), server_default=IncidentStatus.INVESTIGATING.value
    )
    primary_service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id", ondelete="SET NULL"))
    commander_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    root_cause: Mapped[str | None] = mapped_column(Text)
    # The error signature that characterises the incident (e.g. GatewayTimeoutException); used for similarity.
    signature: Mapped[str | None] = mapped_column(String(120), index=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    primary_service: Mapped[Service | None] = relationship(lazy="joined")
    commander: Mapped[User | None] = relationship(lazy="joined")
    affected_services: Mapped[list[Service]] = relationship(secondary=incident_services, order_by=lambda: Service.name)
    tickets: Mapped[list[Ticket]] = relationship(secondary="incident_tickets", viewonly=True)

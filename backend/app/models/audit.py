from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditEvent(Base):
    """Append-only security/compliance log: who did what to which entity, written in the same transaction."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
        Index("ix_audit_events_actor_occurred", "actor_id", text("occurred_at DESC")),
        Index("ix_audit_events_occurred", text("occurred_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64))  # e.g. ticket.status_changed, user.deleted
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    ip_address: Mapped[str | None] = mapped_column(INET)

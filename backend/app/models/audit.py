from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AuditEvent(Base):
    """Append-only security/compliance log: who did what to which entity, written in the same transaction.

    Tamper-evident (migration 0009): a trigger places each row in a hash chain (`chain_seq`, `prev_hash`, `hash`)
    and refuses UPDATE/DELETE/TRUNCATE, so the application cannot rewrite history and a direct database edit is
    detected by `AuditService.verify()`. The three chain columns are written by the database only.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
        Index("ix_audit_events_actor_occurred", "actor_id", text("occurred_at DESC")),
        Index("ix_audit_events_occurred", text("occurred_at DESC")),
        Index("ix_audit_events_action", "action"),
        UniqueConstraint("chain_seq"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # RESTRICT: people with audit history are deactivated (soft delete), never erased.
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(String(64))  # e.g. ticket.status_changed, user.deleted
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    ip_address: Mapped[str | None] = mapped_column(INET)
    chain_seq: Mapped[int | None] = mapped_column(BigInteger)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str | None] = mapped_column(String(64))

    actor: Mapped["User | None"] = relationship(lazy="joined")  # noqa: F821

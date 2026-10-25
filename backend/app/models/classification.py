from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import ClassificationStatus, Priority, TicketCategory, db_enum
from app.models.service import Service
from app.models.user import User


class TicketClassification(Base):
    """An AI triage suggestion for a ticket and what the human analyst did with it.

    The prediction columns are never edited: the analyst's decision is stored next to them (final_*), so every row
    is a labelled example — acceptance rate and the fields people correct most are queries, not guesses.
    """

    __tablename__ = "ticket_classifications"
    __table_args__ = (
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        Index("ix_ticket_classifications_ticket_created", "ticket_id", text("created_at DESC")),
        Index("ix_ticket_classifications_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"))
    engine: Mapped[str] = mapped_column(String(64))  # "keywords-v1" or the LLM model
    # Prediction
    category: Mapped[TicketCategory] = mapped_column(db_enum(TicketCategory, "ticket_category"))
    priority: Mapped[Priority] = mapped_column(db_enum(Priority, "priority"))
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id", ondelete="SET NULL"))
    confidence: Mapped[float] = mapped_column(Numeric(4, 3, asdecimal=False))
    rationale: Mapped[str] = mapped_column(Text, default="")
    signals: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")  # what the prediction was based on
    # Human review
    status: Mapped[ClassificationStatus] = mapped_column(
        db_enum(ClassificationStatus, "classification_status"), server_default=ClassificationStatus.PENDING.value
    )
    final_category: Mapped[TicketCategory | None] = mapped_column(db_enum(TicketCategory, "final_category"))
    final_priority: Mapped[Priority | None] = mapped_column(db_enum(Priority, "final_priority"))
    final_service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id", ondelete="SET NULL"))
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    service: Mapped[Service | None] = relationship(foreign_keys=[service_id], lazy="joined")
    final_service: Mapped[Service | None] = relationship(foreign_keys=[final_service_id], lazy="joined")
    reviewed_by: Mapped[User | None] = relationship(lazy="joined")

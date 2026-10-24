from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import EvidenceKind, EvidenceStance, InvestigationStatus, db_enum
from app.models.user import User


class AIInvestigation(Base):
    """One run of the investigation engine against an incident. Immutable once COMPLETED."""

    __tablename__ = "ai_investigations"
    __table_args__ = (
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="confidence_range"),
        Index("ix_ai_investigations_incident_created", "incident_id", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[InvestigationStatus] = mapped_column(
        db_enum(InvestigationStatus, "investigation_status"), server_default=InvestigationStatus.RUNNING.value
    )
    engine: Mapped[str] = mapped_column(String(40))  # e.g. "rules-v1"; later "rag-claude"
    hypothesis_key: Mapped[str | None] = mapped_column(String(64))
    root_cause: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3, asdecimal=False))
    # Ordered lists whose shape is owned by the engine version — JSONB keeps them with the run that produced them.
    recommended_steps: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    suggested_sql: Mapped[str | None] = mapped_column(Text)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    requested_by: Mapped[User | None] = relationship(lazy="joined")
    evidence: Mapped[list["AIEvidence"]] = relationship(
        back_populates="investigation", order_by="AIEvidence.position", cascade="all, delete-orphan"
    )


class AIEvidence(Base):
    """A single, verifiable fact an investigation relied on (or that argued against it)."""

    __tablename__ = "ai_evidence"
    __table_args__ = (Index("ix_ai_evidence_investigation_position", "investigation_id", "position"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("ai_investigations.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(SmallInteger)
    kind: Mapped[EvidenceKind] = mapped_column(db_enum(EvidenceKind, "evidence_kind"))
    stance: Mapped[EvidenceStance] = mapped_column(db_enum(EvidenceStance, "evidence_stance"))
    summary: Mapped[str] = mapped_column(Text)
    weight: Mapped[float] = mapped_column(Double, server_default="0")
    # Raw numbers behind the summary (counts, baseline vs. current, sample log ids) so the UI can show "why".
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    related_incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="SET NULL"))

    investigation: Mapped[AIInvestigation] = relationship(back_populates="evidence")
    related_incident: Mapped["Incident | None"] = relationship(lazy="joined")  # noqa: F821
    document: Mapped["KnowledgeDocument | None"] = relationship(lazy="joined")  # noqa: F821

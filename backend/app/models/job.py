import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, SmallInteger, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import JobStatus, db_enum


class BackgroundJob(Base):
    """Durable record of a background job. Redis carries the message; PostgreSQL is the source of truth.

    If Redis loses a message (restart without persistence) the row still says QUEUED, so a stuck job is visible
    and can be re-enqueued, and the admin page can show throughput and failures without querying Redis.
    """

    __tablename__ = "background_jobs"
    __table_args__ = (
        Index("ix_background_jobs_status_enqueued", "status", text("enqueued_at DESC")),
        Index("ix_background_jobs_entity", "entity_type", "entity_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(40))  # "ticket.triage", "incident.investigate"
    status: Mapped[JobStatus] = mapped_column(db_enum(JobStatus, "job_status"), server_default=JobStatus.QUEUED.value)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    worker: Mapped[str | None] = mapped_column(String(64))
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

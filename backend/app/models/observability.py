from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Double, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import LogLevel, db_enum


class ApplicationLog(Base):
    """A sample of application logs shipped from the platform (the simulator stands in for Loki/Elastic)."""

    __tablename__ = "application_logs"
    __table_args__ = (
        # "Logs of service X in window [a, b]" is the investigation engine's hottest query.
        Index("ix_application_logs_service_ts", "service_id", text("ts DESC")),
        # Partial index: problems are rare, so WARN/ERROR lookups stay small and fast.
        Index(
            "ix_application_logs_problems",
            "service_id",
            "ts",
            postgresql_where=text("level IN ('WARN', 'ERROR')"),
        ),
        # BRIN suits append-only, time-ordered data: tiny index, efficient range scans for retention jobs.
        Index("ix_application_logs_ts_brin", "ts", postgresql_using="brin"),
        Index("ix_application_logs_trace_id", "trace_id", postgresql_where=text("trace_id IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    level: Mapped[LogLevel] = mapped_column(db_enum(LogLevel, "log_level"))
    message: Mapped[str] = mapped_column(Text)
    # Structured fields differ per log line (order_id, provider, timeout, ...) — a textbook JSONB use case.
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    trace_id: Mapped[str | None] = mapped_column(String(32))


class MetricSample(Base):
    __tablename__ = "metric_samples"
    __table_args__ = (UniqueConstraint("service_id", "name", "ts", name="uq_metric_samples_series_ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Double)
    unit: Mapped[str] = mapped_column(String(16))

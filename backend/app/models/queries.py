from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import QueryRisk, QueryRunStatus, db_enum
from app.models.user import User

# --- The ShopStream application database ----------------------------------------------------------------------
# In production this would be a read replica of the e-commerce platform's own database. Here it lives in a separate
# `shopstream` schema that only the `opspilot_readonly` role can read, filled by the seed from the simulated logs.
SHOPSTREAM = "shopstream"

shop_orders = Table(
    "orders",
    Base.metadata,
    Column("id", String(20), primary_key=True),
    Column("merchant_id", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("total", Numeric(10, 2), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    schema=SHOPSTREAM,
)

shop_payments = Table(
    "payments",
    Base.metadata,
    Column("id", BigInteger, primary_key=True),
    Column("order_id", String(20), nullable=False, index=True),
    Column("provider", String(20), nullable=False),
    Column("amount", Numeric(10, 2), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("status", String(20), nullable=False),
    Column("gateway_ms", Integer),
    Column("error", String(120)),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    schema=SHOPSTREAM,
)

shop_password_resets = Table(
    "password_reset_requests",
    Base.metadata,
    Column("id", BigInteger, primary_key=True),
    Column("user_ref", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("error", String(120)),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    schema=SHOPSTREAM,
)


# --- Investigation queries ------------------------------------------------------------------------------------
class InvestigationQuery(Base):
    """A diagnostic SQL query proposed for an investigation, with the safety verdict it was given."""

    __tablename__ = "investigation_queries"
    __table_args__ = (Index("ix_investigation_queries_investigation_position", "investigation_id", "position"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("ai_investigations.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(SmallInteger)
    title: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(Text)  # "Why": what the result would confirm or rule out
    sql: Mapped[str] = mapped_column(Text)  # as proposed
    safe_sql: Mapped[str | None] = mapped_column(Text)  # as it would run (normalised, row limit enforced)
    source: Mapped[str] = mapped_column(String(64))  # "template:<hypothesis>" or the LLM model
    risk: Mapped[QueryRisk] = mapped_column(db_enum(QueryRisk, "query_risk"))
    violations: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    tables: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    runs: Mapped[list["QueryRun"]] = relationship(
        back_populates="query",
        order_by="(QueryRun.executed_at.desc(), QueryRun.id.desc())",
        cascade="all, delete-orphan",
    )

    @property
    def last_run(self) -> "QueryRun | None":
        return self.runs[0] if self.runs else None


class QueryRun(Base):
    """One execution of a query (possibly edited by the analyst), kept for the audit trail."""

    __tablename__ = "query_runs"
    __table_args__ = (Index("ix_query_runs_query_executed", "query_id", "executed_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    query_id: Mapped[int] = mapped_column(ForeignKey("investigation_queries.id", ondelete="CASCADE"))
    executed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    sql: Mapped[str] = mapped_column(Text)  # exactly what ran (or was refused)
    edited: Mapped[bool] = mapped_column(server_default="false")
    status: Mapped[QueryRunStatus] = mapped_column(db_enum(QueryRunStatus, "query_run_status"))
    columns: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    rows: Mapped[list[list[Any]]] = mapped_column(JSONB, server_default="[]")
    row_count: Mapped[int] = mapped_column(Integer, server_default="0")
    truncated: Mapped[bool] = mapped_column(server_default="false")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    query: Mapped[InvestigationQuery] = relationship(back_populates="runs")
    executed_by: Mapped[User | None] = relationship(lazy="joined")

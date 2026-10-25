"""investigation queries and read-only shopstream sandbox

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-25 12:00:08.941367
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READONLY_ROLE = "opspilot_readonly"

# The role investigation queries run as (SET LOCAL ROLE inside a read-only transaction). It can read the ShopStream
# schema and nothing else: OpsPilot's own tables (users, password hashes, tickets) are not granted to it. Roles are
# cluster-wide, so creation is idempotent and the role is left in place on downgrade (other databases may use it).
GRANTS = f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{READONLY_ROLE}') THEN
        CREATE ROLE {READONLY_ROLE} NOLOGIN;
    END IF;
END $$;
GRANT USAGE ON SCHEMA shopstream TO {READONLY_ROLE};
GRANT SELECT ON ALL TABLES IN SCHEMA shopstream TO {READONLY_ROLE};
GRANT {READONLY_ROLE} TO CURRENT_USER;
DO $$
BEGIN
    GRANT pg_read_all_stats TO {READONLY_ROLE};  -- lets DBA-style queries see other sessions in pg_stat_activity
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'pg_read_all_stats not granted: pg_stat_activity will hide other sessions';
END $$;
"""


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS shopstream")
    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=20), nullable=False),
        sa.Column("merchant_id", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        schema="shopstream",
    )
    op.create_index(
        op.f("ix_shopstream_orders_created_at"), "orders", ["created_at"], unique=False, schema="shopstream"
    )
    op.create_table(
        "password_reset_requests",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_ref", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_reset_requests")),
        schema="shopstream",
    )
    op.create_index(
        op.f("ix_shopstream_password_reset_requests_created_at"),
        "password_reset_requests",
        ["created_at"],
        unique=False,
        schema="shopstream",
    )
    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("gateway_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        schema="shopstream",
    )
    op.create_index(
        op.f("ix_shopstream_payments_created_at"), "payments", ["created_at"], unique=False, schema="shopstream"
    )
    op.create_index(
        op.f("ix_shopstream_payments_order_id"), "payments", ["order_id"], unique=False, schema="shopstream"
    )
    op.create_table(
        "investigation_queries",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("investigation_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("safe_sql", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column(
            "risk",
            sa.Enum("READ_ONLY", "BLOCKED", name="query_risk", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("violations", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("tables", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["ai_investigations.id"],
            name=op.f("fk_investigation_queries_investigation_id_ai_investigations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_investigation_queries")),
    )
    op.create_index(
        "ix_investigation_queries_investigation_position",
        "investigation_queries",
        ["investigation_id", "position"],
        unique=False,
    )
    op.create_table(
        "query_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("query_id", sa.BigInteger(), nullable=False),
        sa.Column("executed_by_id", sa.BigInteger(), nullable=True),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("edited", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "SUCCEEDED",
                "FAILED",
                "REJECTED",
                name="query_run_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("columns", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("rows", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("truncated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["executed_by_id"], ["users.id"], name=op.f("fk_query_runs_executed_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["query_id"],
            ["investigation_queries.id"],
            name=op.f("fk_query_runs_query_id_investigation_queries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_query_runs")),
    )
    op.create_index("ix_query_runs_query_executed", "query_runs", ["query_id", "executed_at"], unique=False)
    op.execute(GRANTS)


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA shopstream FROM {READONLY_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA shopstream FROM {READONLY_ROLE}")
    op.drop_index("ix_query_runs_query_executed", table_name="query_runs")
    op.drop_table("query_runs")
    op.drop_index("ix_investigation_queries_investigation_position", table_name="investigation_queries")
    op.drop_table("investigation_queries")
    op.drop_index(op.f("ix_shopstream_payments_order_id"), table_name="payments", schema="shopstream")
    op.drop_index(op.f("ix_shopstream_payments_created_at"), table_name="payments", schema="shopstream")
    op.drop_table("payments", schema="shopstream")
    op.drop_index(
        op.f("ix_shopstream_password_reset_requests_created_at"),
        table_name="password_reset_requests",
        schema="shopstream",
    )
    op.drop_table("password_reset_requests", schema="shopstream")
    op.drop_index(op.f("ix_shopstream_orders_created_at"), table_name="orders", schema="shopstream")
    op.drop_table("orders", schema="shopstream")
    op.execute("DROP SCHEMA IF EXISTS shopstream")

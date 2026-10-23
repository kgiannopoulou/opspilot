"""tickets, services, users

Revision ID: 0001
Revises:
Create Date: 2026-10-23 10:13:33.981950
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Human-friendly ticket numbers (TCK-2026-00042) come from a sequence, not from the primary key.
    op.execute("CREATE SEQUENCE ticket_number_seq")
    op.create_table(
        "services",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("tier", sa.SmallInteger(), nullable=False),
        sa.Column("owner_team", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("tier BETWEEN 1 AND 4", name=op.f("ck_services_tier_range")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_services")),
        sa.UniqueConstraint("name", name=op.f("uq_services_name")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "tickets",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "priority",
            sa.Enum("P1", "P2", "P3", "P4", name="priority", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "TRIAGED",
                "INVESTIGATING",
                "RESOLVED",
                "CLOSED",
                name="ticket_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="OPEN",
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum(
                "customer_support",
                "monitoring",
                "email",
                "internal",
                name="ticket_source",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="customer_support",
            nullable=False,
        ),
        sa.Column("labels", postgresql.ARRAY(sa.String(length=40)), server_default="{}", nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("service_id", sa.BigInteger(), nullable=True),
        sa.Column("reporter_id", sa.BigInteger(), nullable=False),
        sa.Column("assignee_id", sa.BigInteger(), nullable=True),
        sa.Column("triaged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["assignee_id"], ["users.id"], name=op.f("fk_tickets_assignee_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["reporter_id"], ["users.id"], name=op.f("fk_tickets_reporter_id_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["service_id"], ["services.id"], name=op.f("fk_tickets_service_id_services"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
        sa.UniqueConstraint("number", name=op.f("uq_tickets_number")),
    )
    op.create_index("ix_tickets_assignee_id", "tickets", ["assignee_id"], unique=False)
    op.create_index("ix_tickets_service_id", "tickets", ["service_id"], unique=False)
    op.create_index(
        "ix_tickets_status_priority_created",
        "tickets",
        ["status", "priority", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "ticket_comments",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("internal", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name=op.f("fk_ticket_comments_author_id_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_ticket_comments_ticket_id_tickets"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_comments")),
    )
    op.create_index(
        "ix_ticket_comments_ticket_id_created", "ticket_comments", ["ticket_id", "created_at"], unique=False
    )
    op.create_table(
        "ticket_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "type",
            sa.Enum(
                "CREATED",
                "UPDATED",
                "STATUS_CHANGED",
                "PRIORITY_CHANGED",
                "ASSIGNED",
                "COMMENTED",
                "LINKED_TO_INCIDENT",
                name="ticket_event_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("from_value", sa.String(length=120), nullable=True),
        sa.Column("to_value", sa.String(length=120), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_ticket_events_actor_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_ticket_events_ticket_id_tickets"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_events")),
    )
    op.create_index("ix_ticket_events_ticket_id_created", "ticket_events", ["ticket_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ticket_events_ticket_id_created", table_name="ticket_events")
    op.drop_table("ticket_events")
    op.drop_index("ix_ticket_comments_ticket_id_created", table_name="ticket_comments")
    op.drop_table("ticket_comments")
    op.drop_index("ix_tickets_status_priority_created", table_name="tickets")
    op.drop_index("ix_tickets_service_id", table_name="tickets")
    op.drop_index("ix_tickets_assignee_id", table_name="tickets")
    op.drop_table("tickets")
    op.drop_table("users")
    op.drop_table("services")
    op.execute("DROP SEQUENCE ticket_number_seq")

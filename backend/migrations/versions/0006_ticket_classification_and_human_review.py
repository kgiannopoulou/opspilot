"""ticket classification and human review

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-25 11:43:18.309404
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EVENT_TYPES_OLD = (
    "'CREATED', 'UPDATED', 'STATUS_CHANGED', 'PRIORITY_CHANGED', 'ASSIGNED', 'COMMENTED', 'LINKED_TO_INCIDENT'"
)
EVENT_TYPES_NEW = EVENT_TYPES_OLD + ", 'CATEGORY_CHANGED', 'AI_CLASSIFIED', 'AI_SUGGESTION_REVIEWED'"


def _event_types(values: str) -> None:
    op.drop_constraint(op.f("ck_ticket_events_ticket_event_type"), "ticket_events", type_="check")
    op.create_check_constraint("ticket_event_type", "ticket_events", f"type IN ({values})")


def upgrade() -> None:
    op.create_table(
        "ticket_classifications",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("engine", sa.String(length=64), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "PAYMENTS",
                "AUTHENTICATION",
                "ORDERS",
                "NOTIFICATIONS",
                "DATABASE",
                "PERFORMANCE",
                "OTHER",
                name="ticket_category",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.Enum("P1", "P2", "P3", "P4", name="priority", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("service_id", sa.BigInteger(), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3, asdecimal=False), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "ACCEPTED",
                "MODIFIED",
                "REJECTED",
                "SUPERSEDED",
                name="classification_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column(
            "final_category",
            sa.Enum(
                "PAYMENTS",
                "AUTHENTICATION",
                "ORDERS",
                "NOTIFICATIONS",
                "DATABASE",
                "PERFORMANCE",
                "OTHER",
                name="final_category",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column(
            "final_priority",
            sa.Enum(
                "P1", "P2", "P3", "P4", name="final_priority", native_enum=False, create_constraint=True, length=32
            ),
            nullable=True,
        ),
        sa.Column("final_service_id", sa.BigInteger(), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_by_id", sa.BigInteger(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name=op.f("ck_ticket_classifications_confidence_range")),
        sa.ForeignKeyConstraint(
            ["final_service_id"],
            ["services.id"],
            name=op.f("fk_ticket_classifications_final_service_id_services"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"],
            ["users.id"],
            name=op.f("fk_ticket_classifications_reviewed_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_ticket_classifications_service_id_services"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_ticket_classifications_ticket_id_tickets"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_classifications")),
    )
    op.create_index("ix_ticket_classifications_status", "ticket_classifications", ["status"], unique=False)
    op.create_index(
        "ix_ticket_classifications_ticket_created",
        "ticket_classifications",
        ["ticket_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.add_column(
        "tickets",
        sa.Column(
            "category",
            sa.Enum(
                "PAYMENTS",
                "AUTHENTICATION",
                "ORDERS",
                "NOTIFICATIONS",
                "DATABASE",
                "PERFORMANCE",
                "OTHER",
                name="ticket_category",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
    )
    _event_types(EVENT_TYPES_NEW)


def downgrade() -> None:
    op.execute(
        "DELETE FROM ticket_events WHERE type IN ('CATEGORY_CHANGED', 'AI_CLASSIFIED', 'AI_SUGGESTION_REVIEWED')"
    )
    _event_types(EVENT_TYPES_OLD)
    op.drop_column("tickets", "category")
    op.drop_index("ix_ticket_classifications_ticket_created", table_name="ticket_classifications")
    op.drop_index("ix_ticket_classifications_status", table_name="ticket_classifications")
    op.drop_table("ticket_classifications")

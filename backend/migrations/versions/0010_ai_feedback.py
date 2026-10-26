"""ai feedback on investigations

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-26 09:05:26.435954
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REASONS = ("WRONG_ROOT_CAUSE", "MISSING_EVIDENCE", "IRRELEVANT_DOCUMENTS", "UNHELPFUL_STEPS", "WRONG_QUERY",
           "UNSUPPORTED_CLAIM", "OTHER")  # fmt: skip


def upgrade() -> None:
    op.create_table(
        "ai_feedback",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("investigation_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(*REASONS, name="feedback_reason", native_enum=False, create_constraint=True, length=32),
            nullable=True,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("rating = 1 OR reason IS NOT NULL", name=op.f("ck_ai_feedback_negative_needs_reason")),
        sa.CheckConstraint("rating IN (-1, 1)", name=op.f("ck_ai_feedback_rating_values")),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["ai_investigations.id"],
            name=op.f("fk_ai_feedback_investigation_id_ai_investigations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_ai_feedback_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_feedback")),
        sa.UniqueConstraint("investigation_id", "user_id", name=op.f("uq_ai_feedback_investigation_id")),
    )
    op.create_index("ix_ai_feedback_created", "ai_feedback", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ai_feedback_created", table_name="ai_feedback")
    op.drop_table("ai_feedback")

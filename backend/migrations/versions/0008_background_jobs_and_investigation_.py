"""background jobs and investigation progress

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-25 12:12:37.933743
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STATUSES_OLD = "'RUNNING', 'COMPLETED', 'FAILED'"
STATUSES_NEW = "'QUEUED', " + STATUSES_OLD


def _statuses(values: str) -> None:
    op.drop_constraint(op.f("ck_ai_investigations_investigation_status"), "ai_investigations", type_="check")
    op.create_check_constraint("investigation_status", "ai_investigations", f"status IN ({values})")


def upgrade() -> None:
    _statuses(STATUSES_NEW)
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "QUEUED",
                "RUNNING",
                "SUCCEEDED",
                "RETRYING",
                "DEAD",
                name="job_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="QUEUED",
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("worker", sa.String(length=64), nullable=True),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_background_jobs")),
    )
    op.create_index("ix_background_jobs_entity", "background_jobs", ["entity_type", "entity_id"], unique=False)
    op.create_index(
        "ix_background_jobs_status_enqueued",
        "background_jobs",
        ["status", sa.literal_column("enqueued_at DESC")],
        unique=False,
    )
    op.add_column(
        "ai_investigations",
        sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
    )
    op.add_column("ai_investigations", sa.Column("attempts", sa.SmallInteger(), server_default="0", nullable=False))
    op.add_column("ai_investigations", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.execute("UPDATE ai_investigations SET status = 'FAILED', error = 'never ran' WHERE status = 'QUEUED'")
    _statuses(STATUSES_OLD)
    op.drop_column("ai_investigations", "started_at")
    op.drop_column("ai_investigations", "attempts")
    op.drop_column("ai_investigations", "progress")
    op.drop_index("ix_background_jobs_status_enqueued", table_name="background_jobs")
    op.drop_index("ix_background_jobs_entity", table_name="background_jobs")
    op.drop_table("background_jobs")

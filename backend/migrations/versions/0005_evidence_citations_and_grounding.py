"""Evidence citations: per-evidence citation keys, cited passage, grounding report per investigation

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-25 11:02:37.540119
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_evidence", sa.Column("ref", sa.String(length=120), server_default="", nullable=False))
    op.add_column("ai_evidence", sa.Column("chunk_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_ai_evidence_chunk_id_document_chunks"),
        "ai_evidence",
        "document_chunks",
        ["chunk_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "ai_investigations",
        sa.Column("grounding", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("ai_investigations", "grounding")
    op.drop_constraint(op.f("fk_ai_evidence_chunk_id_document_chunks"), "ai_evidence", type_="foreignkey")
    op.drop_column("ai_evidence", "chunk_id")
    op.drop_column("ai_evidence", "ref")

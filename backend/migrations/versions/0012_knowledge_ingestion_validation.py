"""knowledge ingestion validation: document status, scan findings, provenance

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-26 12:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status = sa.Enum("ACTIVE", "QUARANTINED", "REJECTED", name="document_status", native_enum=False,
                     create_constraint=True, length=32)  # fmt: skip
    source = sa.Enum("repository", "upload", name="document_source", native_enum=False, create_constraint=True,
                     length=32)  # fmt: skip
    op.add_column("knowledge_documents", sa.Column("status", status, server_default="ACTIVE", nullable=False))
    op.add_column(
        "knowledge_documents",
        sa.Column("scan_findings", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
    )
    op.add_column("knowledge_documents", sa.Column("source", source, server_default="repository", nullable=False))
    op.add_column("knowledge_documents", sa.Column("uploaded_by_id", sa.BigInteger(), nullable=True))
    op.add_column("knowledge_documents", sa.Column("reviewed_by_id", sa.BigInteger(), nullable=True))
    op.add_column("knowledge_documents", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(op.f("fk_knowledge_documents_uploaded_by_id_users"), "knowledge_documents", "users",
                          ["uploaded_by_id"], ["id"], ondelete="SET NULL")  # fmt: skip
    op.create_foreign_key(op.f("fk_knowledge_documents_reviewed_by_id_users"), "knowledge_documents", "users",
                          ["reviewed_by_id"], ["id"], ondelete="SET NULL")  # fmt: skip


def downgrade() -> None:
    op.drop_constraint(op.f("fk_knowledge_documents_reviewed_by_id_users"), "knowledge_documents", type_="foreignkey")
    op.drop_constraint(op.f("fk_knowledge_documents_uploaded_by_id_users"), "knowledge_documents", type_="foreignkey")
    for column in ("reviewed_at", "reviewed_by_id", "uploaded_by_id", "source", "scan_findings", "status"):
        op.drop_column("knowledge_documents", column)
    op.execute("ALTER TABLE knowledge_documents DROP CONSTRAINT IF EXISTS ck_knowledge_documents_document_status")
    op.execute("ALTER TABLE knowledge_documents DROP CONSTRAINT IF EXISTS ck_knowledge_documents_document_source")

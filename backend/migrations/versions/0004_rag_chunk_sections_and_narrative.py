"""RAG: citable chunk sections, embedding model per chunk, process documents, investigation narrative

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25 09:12:04.118204
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORIES_OLD = "'runbook', 'troubleshooting', 'architecture'"
CATEGORIES_NEW = CATEGORIES_OLD + ", 'process'"


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("section", sa.String(length=16), server_default="0", nullable=False))
    op.add_column("document_chunks", sa.Column("heading", sa.String(length=200), server_default="", nullable=False))
    op.add_column("document_chunks", sa.Column("embedding_model", sa.String(length=64), nullable=True))

    op.drop_constraint(op.f("ck_knowledge_documents_document_category"), "knowledge_documents", type_="check")
    op.create_check_constraint("document_category", "knowledge_documents", f"category IN ({CATEGORIES_NEW})")

    op.add_column("ai_investigations", sa.Column("narrative", sa.Text(), nullable=True))
    op.add_column("ai_investigations", sa.Column("search_query", sa.Text(), nullable=True))
    op.alter_column("ai_investigations", "engine", type_=sa.String(length=64), existing_type=sa.String(length=40))


def downgrade() -> None:
    op.alter_column("ai_investigations", "engine", type_=sa.String(length=40), existing_type=sa.String(length=64))
    op.drop_column("ai_investigations", "search_query")
    op.drop_column("ai_investigations", "narrative")

    op.execute("DELETE FROM knowledge_documents WHERE category = 'process'")
    op.drop_constraint(op.f("ck_knowledge_documents_document_category"), "knowledge_documents", type_="check")
    op.create_check_constraint("document_category", "knowledge_documents", f"category IN ({CATEGORIES_OLD})")

    op.drop_column("document_chunks", "embedding_model")
    op.drop_column("document_chunks", "heading")
    op.drop_column("document_chunks", "section")

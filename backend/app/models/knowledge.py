from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import DocumentCategory, DocumentSource, DocumentStatus, db_enum
from app.models.service import Service

EMBEDDING_DIMENSIONS = 384  # e.g. all-MiniLM-L6-v2; fixed per column, so changing models means a migration


class KnowledgeDocument(TimestampMixin, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (Index("ix_knowledge_documents_tags", "tags", postgresql_using="gin"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[DocumentCategory] = mapped_column(db_enum(DocumentCategory, "document_category"))
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id", ondelete="SET NULL"), index=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), server_default="{}")
    content: Mapped[str] = mapped_column(Text)
    # sha256 of the content: re-chunk/re-embed only when a document actually changed.
    content_hash: Mapped[str] = mapped_column(String(64))
    # Ingestion validation (app/ai/injection.py): only ACTIVE documents are chunked and retrievable.
    status: Mapped[DocumentStatus] = mapped_column(
        db_enum(DocumentStatus, "document_status"), server_default=DocumentStatus.ACTIVE.value
    )
    scan_findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    source: Mapped[DocumentSource] = mapped_column(
        db_enum(DocumentSource, "document_source"), server_default=DocumentSource.REPOSITORY.value
    )
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    service: Mapped[Service | None] = relationship(lazy="joined")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_index"),
        # Approximate nearest-neighbour search for RAG retrieval (cosine distance).
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    # Numbered from the Markdown headings ("2", "2.1"): what an investigation cites, e.g. [RUNBOOK-x §2].
    section: Mapped[str] = mapped_column(String(16), server_default="0")
    heading: Mapped[str] = mapped_column(String(200), server_default="")
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    # Vectors from different models are not comparable: a model switch must re-embed every chunk.
    embedding_model: Mapped[str | None] = mapped_column(String(64))

    document: Mapped[KnowledgeDocument] = relationship()

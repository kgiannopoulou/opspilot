from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.enums import DocumentCategory, DocumentSource, DocumentStatus
from app.schemas.common import ORMModel, ServiceRef


class DocumentSummary(ORMModel):
    id: int
    slug: str
    title: str
    category: DocumentCategory
    service: ServiceRef | None
    tags: list[str]
    status: DocumentStatus
    source: DocumentSource
    updated_at: datetime


class DocumentOut(DocumentSummary):
    content: str


class DocumentReviewOut(DocumentOut):
    """What a reviewer sees: the ingestion scanner's findings next to the document."""

    scan_findings: list[dict[str, Any]]
    uploaded_by_id: int | None
    reviewed_by_id: int | None
    reviewed_at: datetime | None


class DocumentSubmit(BaseModel):
    slug: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    category: DocumentCategory
    markdown: str = Field(min_length=20, max_length=200_000, description="Front matter (title, service, tags) + body")


class DocumentReview(BaseModel):
    action: Literal["release", "reject"]
    note: str = Field(min_length=5, max_length=1_000, description="Why: kept in the audit trail")


class SearchHit(BaseModel):
    chunk_id: int
    slug: str
    title: str
    category: str
    section: str
    heading: str
    content: str
    similarity: float
    score: float


class SearchResults(BaseModel):
    query: str
    embedding_model: str
    hits: list[SearchHit]

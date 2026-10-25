from datetime import datetime

from pydantic import BaseModel

from app.models.enums import DocumentCategory
from app.schemas.common import ORMModel, ServiceRef


class DocumentSummary(ORMModel):
    id: int
    slug: str
    title: str
    category: DocumentCategory
    service: ServiceRef | None
    tags: list[str]
    updated_at: datetime


class DocumentOut(DocumentSummary):
    content: str


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

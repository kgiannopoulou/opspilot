from datetime import datetime

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

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.retrieval import KnowledgeRetriever
from app.auth.dependencies import ai_rate_limit, get_current_user, require
from app.auth.permissions import Permission
from app.core.database import get_db
from app.models import KnowledgeDocument, User
from app.models.enums import DocumentStatus
from app.schemas.knowledge import (
    DocumentOut,
    DocumentReview,
    DocumentReviewOut,
    DocumentSubmit,
    DocumentSummary,
    SearchHit,
    SearchResults,
)
from app.services.knowledge import get_document, review_document, submit_document

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

DB = Annotated[Session, Depends(get_db)]
Authenticated = Annotated[User, Depends(get_current_user)]
Contributor = Annotated[User, Depends(require(Permission.KNOWLEDGE_WRITE))]
Reviewer = Annotated[User, Depends(require(Permission.KNOWLEDGE_REVIEW))]


@router.get("", response_model=list[DocumentSummary])
def list_documents(db: DB, _: Authenticated) -> list[DocumentSummary]:
    docs = db.scalars(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.status == DocumentStatus.ACTIVE)
        .order_by(KnowledgeDocument.category, KnowledgeDocument.title)
    )
    return [DocumentSummary.model_validate(d) for d in docs]


@router.post("", response_model=DocumentReviewOut, status_code=status.HTTP_201_CREATED)
def submit(data: DocumentSubmit, db: DB, user: Contributor) -> DocumentReviewOut:
    """Add a document. It is scanned for prompt injection first: a clean document is indexed at once; one with a
    blocking finding is stored as QUARANTINED (never retrievable) until an admin releases or rejects it."""
    return DocumentReviewOut.model_validate(submit_document(db, user, data.slug, data.category, data.markdown))


@router.get("/quarantine", response_model=list[DocumentReviewOut])
def quarantine(db: DB, _: Reviewer) -> list[DocumentReviewOut]:
    """Documents held back by ingestion validation, with the scanner's findings."""
    docs = db.scalars(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.status != DocumentStatus.ACTIVE)
        .order_by(KnowledgeDocument.updated_at.desc())
    )
    return [DocumentReviewOut.model_validate(d) for d in docs]


@router.post("/{slug}/review", response_model=DocumentReviewOut)
def review(slug: str, data: DocumentReview, db: DB, user: Reviewer) -> DocumentReviewOut:
    return DocumentReviewOut.model_validate(review_document(db, user, slug, data.action, data.note))


@router.get("/search", response_model=SearchResults, dependencies=[Depends(ai_rate_limit)])
def search_knowledge(
    db: DB,
    _: Authenticated,
    q: str = Query(min_length=2, max_length=300),
    k: int = Query(default=5, ge=1, le=20),
    service: str | None = None,
) -> SearchResults:
    """Semantic search over knowledge-base passages: the same retrieval an investigation uses."""
    retriever = KnowledgeRetriever(db)
    hits = retriever.search(q, k=k, service=service)
    return SearchResults(
        query=q,
        embedding_model=retriever.embedder.name,
        hits=[SearchHit(**{f: getattr(h, f) for f in SearchHit.model_fields}) for h in hits],
    )


@router.get("/{slug}", response_model=DocumentOut)
def read_document(slug: str, db: DB, _: Authenticated) -> DocumentOut:
    return DocumentOut.model_validate(get_document(db, slug))

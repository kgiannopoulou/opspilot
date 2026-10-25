from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.retrieval import KnowledgeRetriever
from app.auth.dependencies import get_current_user
from app.core.database import get_db
from app.models import KnowledgeDocument, User
from app.schemas.knowledge import DocumentOut, DocumentSummary, SearchHit, SearchResults
from app.services.knowledge import get_document

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

DB = Annotated[Session, Depends(get_db)]
Authenticated = Annotated[User, Depends(get_current_user)]


@router.get("", response_model=list[DocumentSummary])
def list_documents(db: DB, _: Authenticated) -> list[DocumentSummary]:
    docs = db.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.category, KnowledgeDocument.title))
    return [DocumentSummary.model_validate(d) for d in docs]


@router.get("/search", response_model=SearchResults)
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

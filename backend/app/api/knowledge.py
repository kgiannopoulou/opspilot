from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.core.database import get_db
from app.models import KnowledgeDocument, User
from app.schemas.knowledge import DocumentOut, DocumentSummary
from app.services.knowledge import get_document

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

DB = Annotated[Session, Depends(get_db)]
Authenticated = Annotated[User, Depends(get_current_user)]


@router.get("", response_model=list[DocumentSummary])
def list_documents(db: DB, _: Authenticated) -> list[DocumentSummary]:
    docs = db.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.category, KnowledgeDocument.title))
    return [DocumentSummary.model_validate(d) for d in docs]


@router.get("/{slug}", response_model=DocumentOut)
def read_document(slug: str, db: DB, _: Authenticated) -> DocumentOut:
    return DocumentOut.model_validate(get_document(db, slug))

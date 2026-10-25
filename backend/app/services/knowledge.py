"""Load the Markdown knowledge base (knowledge-base/) into knowledge_documents.

Each file starts with a front-matter block:

    ---
    title: Payment Gateway Timeout Runbook
    service: payment-service
    tags: [payments, gateway, timeout]
    ---

The folder decides the category (runbooks/ → runbook, ...). Loading is idempotent: a document is only rewritten when
its sha256 content hash changes, and only then re-chunked and re-embedded. A change of embedding model re-embeds every
chunk, because vectors from two models cannot be compared.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.chunking import chunk_markdown
from app.ai.embeddings import Embedder, get_embedder
from app.core.errors import NotFoundError
from app.models import DocumentChunk, KnowledgeDocument, Service
from app.models.enums import DocumentCategory

KNOWLEDGE_BASE_DIR = Path(__file__).resolve().parents[3] / "knowledge-base"
FOLDER_CATEGORY = {
    "runbooks": DocumentCategory.RUNBOOK,
    "troubleshooting": DocumentCategory.TROUBLESHOOTING,
    "architecture": DocumentCategory.ARCHITECTURE,
    "process": DocumentCategory.PROCESS,
}


@dataclass(frozen=True)
class ParsedDocument:
    slug: str
    title: str
    category: DocumentCategory
    service: str | None
    tags: list[str]
    content: str


def parse_document(path: Path, category: DocumentCategory) -> ParsedDocument:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---\n"):
        header, _, body = text[4:].partition("\n---\n")
        for line in header.splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    tags = [t.strip() for t in meta.get("tags", "").strip("[]").split(",") if t.strip()]
    return ParsedDocument(
        slug=path.stem,
        title=meta.get("title") or path.stem.replace("-", " ").title(),
        category=category,
        service=meta.get("service") or None,
        tags=tags,
        content=body.strip() + "\n",
    )


def index_document(db: Session, doc: KnowledgeDocument, embedder: Embedder) -> int:
    """Replace the document's chunks with freshly split and embedded ones. Returns the number of chunks."""
    db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete(synchronize_session=False)
    chunks = chunk_markdown(doc.title, doc.content)
    vectors = embedder.embed([c.embed_text for c in chunks])
    db.add_all(
        DocumentChunk(
            document_id=doc.id,
            chunk_index=c.index,
            section=c.section,
            heading=c.heading[:200],
            content=c.content,
            token_count=c.token_count,
            embedding=vector,
            embedding_model=embedder.name,
        )
        for c, vector in zip(chunks, vectors, strict=True)
    )
    return len(chunks)


def load_knowledge_base(
    db: Session, root: Path = KNOWLEDGE_BASE_DIR, embedder: Embedder | None = None
) -> dict[str, int]:
    """Insert new documents, update changed ones and (re)index whatever is stale. Returns counts per outcome."""
    embedder = embedder or get_embedder()
    services = {s.name: s.id for s in db.scalars(select(Service))}
    existing = {d.slug: d for d in db.scalars(select(KnowledgeDocument))}
    # Documents whose chunks are missing or were embedded by another model.
    stale = set(
        db.scalars(
            select(KnowledgeDocument.slug).where(
                ~select(DocumentChunk.id)
                .where(
                    DocumentChunk.document_id == KnowledgeDocument.id,
                    DocumentChunk.embedding_model == embedder.name,
                )
                .exists()
            )
        )
    )
    stats = {"created": 0, "updated": 0, "unchanged": 0, "reindexed": 0, "chunks": 0}
    for folder, category in FOLDER_CATEGORY.items():
        for path in sorted((root / folder).glob("*.md")):
            doc = parse_document(path, category)
            digest = hashlib.sha256(doc.content.encode()).hexdigest()
            row = existing.get(doc.slug)
            if row is not None and row.content_hash == digest:
                stats["unchanged"] += 1
                if doc.slug in stale:
                    stats["chunks"] += index_document(db, row, embedder)
                    stats["reindexed"] += 1
                continue
            if row is None:
                row = KnowledgeDocument(slug=doc.slug)
                db.add(row)
                stats["created"] += 1
            else:
                stats["updated"] += 1
            row.title, row.category, row.tags, row.content = doc.title, doc.category, doc.tags, doc.content
            row.service_id = services.get(doc.service) if doc.service else None
            row.content_hash = digest
            db.flush()
            stats["chunks"] += index_document(db, row, embedder)
            stats["reindexed"] += 1
    db.commit()
    return stats


def get_document(db: Session, slug: str) -> KnowledgeDocument:
    doc = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.slug == slug))
    if doc is None:
        raise NotFoundError(f"document {slug} not found")
    return doc

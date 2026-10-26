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

Ingestion validation: every document, from the repository or uploaded, is scanned for prompt injection on every load
(detectors improve; old documents are re-checked). A document with a blocking finding is QUARANTINED: kept for review
with its findings, but its chunks are deleted, so retrieval can never hand it to a model. A reviewer can release it
(the release holds until its content changes) or reject it.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import injection
from app.ai.chunking import chunk_markdown
from app.ai.embeddings import Embedder, get_embedder
from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.models import DocumentChunk, KnowledgeDocument, Service, User
from app.models.enums import DocumentCategory, DocumentSource, DocumentStatus
from app.services import audit

log = logging.getLogger(__name__)

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
    return parse_markdown(path.stem, path.read_text(encoding="utf-8"), category)


def parse_markdown(slug: str, text: str, category: DocumentCategory) -> ParsedDocument:
    text = text.replace("\r\n", "\n")
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---\n"):
        header, _, body = text[4:].partition("\n---\n")
        for line in header.splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    tags = [t.strip() for t in meta.get("tags", "").strip("[]").split(",") if t.strip()]
    return ParsedDocument(
        slug=slug,
        title=meta.get("title") or slug.replace("-", " ").title(),
        category=category,
        service=meta.get("service") or None,
        tags=tags,
        content=body.strip() + "\n",
    )


def index_document(db: Session, doc: KnowledgeDocument, embedder: Embedder) -> int:
    """Replace the document's chunks with freshly split and embedded ones. Returns the number of chunks."""
    db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete(synchronize_session=False)
    if doc.status != DocumentStatus.ACTIVE:
        return 0  # quarantined / rejected documents have no chunks: nothing to retrieve
    # Indexed text is sanitised (no invisible characters or HTML comments); `content` keeps the original for review.
    chunks = chunk_markdown(doc.title, injection.sanitize(doc.content))
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
    stats = {"created": 0, "updated": 0, "unchanged": 0, "reindexed": 0, "chunks": 0, "quarantined": 0}
    for folder, category in FOLDER_CATEGORY.items():
        for path in sorted((root / folder).glob("*.md")):
            doc = parse_document(path, category)
            digest = hashlib.sha256(doc.content.encode()).hexdigest()
            row = existing.get(doc.slug)
            if row is not None and row.content_hash == digest:
                stats["unchanged"] += 1
                was_active = row.status == DocumentStatus.ACTIVE
                active = validate(db, row)  # re-scan: the detectors may have improved since it was loaded
                if (active and (doc.slug in stale or not was_active)) or (was_active and not active):
                    stats["chunks"] += index_document(db, row, embedder)
                    stats["reindexed"] += 1
                stats["quarantined"] += 0 if active else 1
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
            row.reviewed_by_id = row.reviewed_at = None  # a review covers the content it saw, not later edits
            db.flush()
            validate(db, row)
            stats["chunks"] += index_document(db, row, embedder)
            stats["reindexed"] += 1
            if row.status != DocumentStatus.ACTIVE:
                stats["quarantined"] += 1
    db.commit()
    return stats


def validate(db: Session, row: KnowledgeDocument, actor: User | None = None) -> bool:
    """Scan the document and set its status; True when it may be indexed. A reviewer's release or rejection of this
    exact content is respected; blocking findings on unreviewed content quarantine it."""
    findings = injection.scan(f"{row.title}\n{row.content}")
    row.scan_findings = [f.as_dict() for f in findings]
    previous = row.status
    if row.reviewed_by_id is not None and previous in (DocumentStatus.ACTIVE, DocumentStatus.REJECTED):
        return previous == DocumentStatus.ACTIVE
    row.status = DocumentStatus.QUARANTINED if injection.is_blocked(findings) else DocumentStatus.ACTIVE
    if row.status == DocumentStatus.QUARANTINED and previous != DocumentStatus.QUARANTINED:
        blocking = [f.detector for f in findings if f.severity == "block"]
        log.warning("knowledge document %s quarantined: %s", row.slug, blocking)
        audit.record(db, actor, "knowledge.quarantined", "knowledge_document", row.slug,
                     {"findings": blocking, "source": str(row.source or DocumentSource.REPOSITORY)})  # fmt: skip
    return row.status == DocumentStatus.ACTIVE


def get_document(db: Session, slug: str, include_inactive: bool = False) -> KnowledgeDocument:
    doc = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.slug == slug))
    if doc is None or (not include_inactive and doc.status != DocumentStatus.ACTIVE):
        raise NotFoundError(f"document {slug} not found")
    return doc


MAX_UPLOAD_BYTES = 200_000


def submit_document(
    db: Session, actor: User, slug: str, category: DocumentCategory, markdown: str, embedder: Embedder | None = None
) -> KnowledgeDocument:
    """POST /knowledge: a document from a person, validated exactly like repository documents. A suspicious upload
    is stored as QUARANTINED for an admin and never indexed."""
    if len(markdown.encode()) > MAX_UPLOAD_BYTES:
        raise ValidationFailed(f"documents are limited to {MAX_UPLOAD_BYTES} bytes")
    if db.scalar(select(KnowledgeDocument.id).where(KnowledgeDocument.slug == slug)) is not None:
        raise ConflictError(f"document {slug} already exists")
    doc = parse_markdown(slug, markdown, category)
    if len(doc.content.strip()) < 20:
        raise ValidationFailed("the document has no content")
    service_id = None
    if doc.service:
        service_id = db.scalar(select(Service.id).where(Service.name == doc.service))
        if service_id is None:
            raise ValidationFailed(f"unknown service {doc.service}")
    row = KnowledgeDocument(
        slug=slug,
        title=doc.title[:200],
        category=category,
        tags=[t[:40] for t in doc.tags][:20],
        content=doc.content,
        service_id=service_id,
        content_hash=hashlib.sha256(doc.content.encode()).hexdigest(),
        status=DocumentStatus.ACTIVE,
        source=DocumentSource.UPLOAD,
        uploaded_by_id=actor.id,
    )
    db.add(row)
    db.flush()
    validate(db, row, actor)
    chunks = index_document(db, row, embedder or get_embedder())
    audit.record(db, actor, "knowledge.submitted", "knowledge_document", slug,
                 {"status": row.status.value, "chunks": chunks,
                  "findings": [f["detector"] for f in row.scan_findings]})  # fmt: skip
    db.commit()
    return row


def review_document(
    db: Session, actor: User, slug: str, action: str, note: str, embedder: Embedder | None = None
) -> KnowledgeDocument:
    """Release (index despite the findings) or reject a quarantined document. Both are audited with the note."""
    row = get_document(db, slug, include_inactive=True)
    if row.status != DocumentStatus.QUARANTINED:
        raise ConflictError(f"document {slug} is {row.status.value}, not QUARANTINED")
    row.status = DocumentStatus.ACTIVE if action == "release" else DocumentStatus.REJECTED
    row.reviewed_by_id, row.reviewed_at = actor.id, datetime.now(UTC)
    chunks = index_document(db, row, embedder or get_embedder())
    findings = [f["detector"] for f in row.scan_findings]
    action_name = "knowledge.released" if action == "release" else "knowledge.rejected"
    changes = {"note": note, "findings": findings, "chunks": chunks}
    audit.record(db, actor, action_name, "knowledge_document", slug, changes)
    db.commit()
    return row

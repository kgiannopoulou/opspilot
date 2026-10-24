"""Load the Markdown knowledge base (knowledge-base/) into knowledge_documents.

Each file starts with a front-matter block:

    ---
    title: Payment Gateway Timeout Runbook
    service: payment-service
    tags: [payments, gateway, timeout]
    ---

The folder decides the category (runbooks/ → runbook, ...). Loading is idempotent: a document is only rewritten when
its sha256 content hash changes, which is also what will trigger re-chunking and re-embedding for semantic search.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models import KnowledgeDocument, Service
from app.models.enums import DocumentCategory

KNOWLEDGE_BASE_DIR = Path(__file__).resolve().parents[3] / "knowledge-base"
FOLDER_CATEGORY = {
    "runbooks": DocumentCategory.RUNBOOK,
    "troubleshooting": DocumentCategory.TROUBLESHOOTING,
    "architecture": DocumentCategory.ARCHITECTURE,
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


def load_knowledge_base(db: Session, root: Path = KNOWLEDGE_BASE_DIR) -> dict[str, int]:
    """Insert new documents and update changed ones. Returns counts per outcome."""
    services = {s.name: s.id for s in db.scalars(select(Service))}
    existing = {d.slug: d for d in db.scalars(select(KnowledgeDocument))}
    stats = {"created": 0, "updated": 0, "unchanged": 0}
    for folder, category in FOLDER_CATEGORY.items():
        for path in sorted((root / folder).glob("*.md")):
            doc = parse_document(path, category)
            digest = hashlib.sha256(doc.content.encode()).hexdigest()
            row = existing.get(doc.slug)
            if row is not None and row.content_hash == digest:
                stats["unchanged"] += 1
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
    db.commit()
    return stats


def get_document(db: Session, slug: str) -> KnowledgeDocument:
    doc = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.slug == slug))
    if doc is None:
        raise NotFoundError(f"document {slug} not found")
    return doc

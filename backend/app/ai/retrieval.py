"""Retrieval half of RAG: turn an incident into a search query and find the knowledge-base passages that answer it.

    incident ─► search query ─► embedding ─► pgvector (HNSW, cosine) ─► re-rank ─► passages

Ranking is hybrid. The vector similarity does most of the work; two small, explainable boosts break ties the way
an engineer would: passages from a document about the affected service, and documents tagged with the error
signature (tags are curated, so an exact tag hit is strong evidence of relevance). At most two passages per document
keep one long runbook from crowding out everything else.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.embeddings import Embedder, get_embedder
from app.models import DocumentChunk, KnowledgeDocument, Service

CANDIDATES = 24  # nearest neighbours fetched from the index before re-ranking
SERVICE_BOOST = 0.05
TAG_BOOST = 0.08
MAX_PER_DOCUMENT = 2
MIN_SCORE = 0.12  # below this the passage is noise, however few results there are


@dataclass(frozen=True)
class Passage:
    chunk_id: int
    document_id: int
    slug: str
    title: str
    category: str
    section: str
    heading: str
    content: str
    similarity: float  # cosine similarity from the vector index
    score: float  # after boosts; what results are ordered by


def build_query(*parts: str | None) -> str:
    """Join the non-empty parts, dropping repeated words: the query should describe the incident, not shout it."""
    seen: set[str] = set()
    words: list[str] = []
    for part in parts:
        for word in (part or "").split():
            key = word.lower().strip(".,:;()'\"")
            if key and key not in seen:
                seen.add(key)
                words.append(word)
    return " ".join(words)


class KnowledgeRetriever:
    def __init__(self, db: Session, embedder: Embedder | None = None) -> None:
        self.db = db
        self.embedder = embedder or get_embedder()

    def search(
        self,
        query: str,
        k: int = 4,
        service: str | None = None,
        tags: tuple[str, ...] = (),
        per_document: int = MAX_PER_DOCUMENT,
    ) -> list[Passage]:
        """`per_document` caps passages per document (investigations use 1: three documents beat two sections)."""
        if not query.strip():
            return []
        vector = self.embedder.embed([query])[0]
        distance = DocumentChunk.embedding.cosine_distance(vector).label("distance")
        rows = self.db.execute(
            select(DocumentChunk, KnowledgeDocument, Service.name, distance)
            .join(KnowledgeDocument, KnowledgeDocument.id == DocumentChunk.document_id)
            .outerjoin(Service, Service.id == KnowledgeDocument.service_id)
            .where(DocumentChunk.embedding_model == self.embedder.name)
            .order_by(distance)
            .limit(CANDIDATES)
        ).all()

        wanted_tags = {t.lower() for t in tags}
        ranked: list[Passage] = []
        for chunk, doc, service_name, dist in rows:
            similarity = 1 - float(dist)
            boost = SERVICE_BOOST if service and service_name == service else 0.0
            if wanted_tags & {t.lower() for t in doc.tags}:
                boost += TAG_BOOST
            ranked.append(
                Passage(
                    chunk_id=chunk.id,
                    document_id=doc.id,
                    slug=doc.slug,
                    title=doc.title,
                    category=doc.category.value,
                    section=chunk.section,
                    heading=chunk.heading,
                    content=chunk.content,
                    similarity=round(similarity, 4),
                    score=round(similarity + boost, 4),
                )
            )
        ranked.sort(key=lambda p: (-p.score, p.chunk_id))

        out: list[Passage] = []
        per_doc: dict[int, int] = {}
        for passage in ranked:
            if passage.score < MIN_SCORE or per_doc.get(passage.document_id, 0) >= per_document:
                continue
            per_doc[passage.document_id] = per_doc.get(passage.document_id, 0) + 1
            out.append(passage)
            if len(out) == k:
                break
        return out

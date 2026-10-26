# ADR 002 — Use pgvector for semantic search

**Status:** accepted · **Date:** 2026-10-25 · **Supersedes:** part of the former ADR 0002

## Context

Investigations need the relevant *passage* of a runbook for the failure at hand, found from customer language
("checkout freezes") rather than tags. That requires embedding document chunks and running nearest-neighbour search.
The knowledge base is small (tens of documents, hundreds of chunks today; thousands at a large company), and every
retrieved chunk is cited as evidence and joined with the investigation that used it.

## Decision

Store embeddings in PostgreSQL with the **pgvector** extension: `document_chunks.embedding vector(384)` with an
**HNSW** index (`vector_cosine_ops`), queried with the cosine distance operator from SQLAlchemy.

- The embedding model is recorded per chunk (`embedding_model`); retrieval only compares vectors from the active
  model, and a model switch re-embeds everything automatically.
- Retrieval is hybrid in SQL + Python: cosine similarity over the HNSW candidates, then small, documented boosts for
  the incident's service and matching tags ([ADR 005](005-rag-retrieval-strategy.md)).
- Quarantined documents have no chunks, and the retrieval query also filters on document status — retrieval cannot
  return a document that failed ingestion validation.

## Alternatives considered

| Option | Why not |
|---|---|
| Dedicated vector database (Pinecone, Weaviate, Qdrant, Milvus) | Another service to run, secure and back up; no transactional link between a chunk and the evidence row that cites it; deletes/quarantine must be synchronised across systems. Worth it at tens of millions of vectors, not here |
| Elasticsearch / OpenSearch kNN | Good hybrid search, but a second cluster for one feature |
| FAISS / in-process index | Fast, but rebuilt on every start, not shared between API and workers, no persistence or filtering by SQL predicates |
| No vectors (BM25 / tags only) | Tried first (Step 10): customer wording rarely matches tags; the retrieval benchmark is the measure of what embeddings add |

## Consequences

- One query joins chunk, document, service and similarity; citations point at rows that are guaranteed to exist.
- HNSW gives approximate results; at this size recall is effectively exact, and the evaluation suite measures it
  (Recall@3 = 100 % on 40 labelled queries).
- The vector dimension is fixed per column: switching to a model with a different dimension is a migration, which is
  why the default hashing embedder and `bge-small-en-v1.5` were chosen with the same 384 dimensions.
- Scaling limits are PostgreSQL's: past a few million chunks, partitioning or a dedicated vector store would be
  re-evaluated — the retriever is a single class, so the swap is local.

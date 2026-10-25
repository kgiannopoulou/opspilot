# ADR 0005 — Retrieval-augmented write-ups on top of the evidence engine

**Status:** accepted

## Context
Step 10 produced scored, evidence-backed investigations, but documentation was matched by tags only and the
write-up was a template. Engineers describe problems in customer language ("checkout freezes") that rarely matches
a tag, and a good explanation needs the relevant *paragraph* of a runbook, not just its title.

## Decision
- Knowledge-base documents are split into heading-aligned chunks, each with a section number that can be cited.
- Chunks are embedded and stored in `pgvector` (384 dimensions, HNSW, cosine). Retrieval is hybrid: vector
  similarity plus small, documented boosts for service and tag matches.
- The embedding model is pluggable and recorded per chunk. The default is a deterministic hashing embedder so the
  whole system, including CI, runs with no model download or API key; a sentence-embedding model can be enabled by
  configuration and the index rebuilds itself.
- The LLM sits *after* the evidence engine. It receives only the collected evidence and retrieved passages, must
  answer in a JSON schema, and is replaceable by an extractive writer. Any LLM failure degrades to that writer.

## Consequences
- Investigations keep working (and stay testable) with no LLM configured.
- Retrieval quality with the default embedder is lexical; paraphrase-heavy queries benefit from `fastembed`.
- The hypothesis and confidence never come from the LLM, so a hallucinated cause cannot raise confidence.

# ADR 005 — RAG retrieval strategy: heading-aligned chunks, hybrid scoring, evidence first

**Status:** accepted · **Date:** 2026-10-25 · **Revised:** 2026-10-26 (one passage per document; untrusted-context isolation) · **Supersedes:** former ADR 0005

## Context

Investigations need the relevant runbook passage, cited precisely enough that an engineer can check it in seconds
(`[RUNBOOK-payment-gateway-timeout §2]`). Engineers and customers describe symptoms in their own words. The knowledge
base is small and curated, but documents can be wrong, stale or — as the threat model assumes — malicious. And the
answer must not depend on the LLM being available.

## Decision

1. **Chunking on headings.** Split Markdown on `##`/`###` so each chunk is one numbered section (`§2`, `§2.1`) that can
   be cited; split long sections on paragraphs; prefix the embedded text with *document › heading* so "1. Check the
   status page" keeps its meaning.
2. **Pluggable embeddings, recorded per chunk.** Default `hashing-v1` (deterministic feature hashing with camelCase
   splitting, stemming and an ops synonym table) so CI and laptops need no model download; `fastembed`
   (`bge-small-en-v1.5`, same 384 dimensions) by configuration. A model switch re-embeds automatically.
3. **Hybrid scoring.** pgvector cosine similarity over HNSW candidates ([ADR 002](002-use-pgvector.md)), plus small,
   explainable boosts for the incident's service and for curated tags matching the error signature.
4. **Diversity.** Investigations take **one passage per document** (search API: two), because the evaluation showed two
   sections of one troubleshooting guide crowding the runbook for the fault out of the top 3.
5. **Query from evidence, not the title alone.** The search query is built from the incident title, the matched log
   signature and corroborating messages, and stored on the investigation so retrieval is auditable.
6. **Retrieval after evidence.** The evidence engine decides the hypothesis and confidence from logs and metrics
   ([ADR 006](006-evidence-first-investigations.md)); retrieved passages *explain and advise*, they never raise
   confidence. The LLM writer sees only stored evidence and passages, must cite them, and is checked by the grounding
   check; an extractive writer is the fallback.
7. **Retrieved text is untrusted.** Only documents that passed ingestion validation are indexed; passages are placed in
   `<untrusted_document>` blocks the system prompt declares to be data ([ADR 010](010-prompt-injection-defense.md)).

## Alternatives considered

| Option | Why not |
|---|---|
| Fixed-size token windows with overlap | Chunks straddle sections, so citations cannot name a section; overlap duplicates passages in the top k |
| Whole-document retrieval | Documents are long; the model (and the engineer) would get pages instead of the paragraph that matters |
| Pure vector similarity | Lost to hybrid on the benchmark for service-specific runbooks; boosts are tiny, documented and testable |
| Cross-encoder re-ranker | Better ranking, but a second model to ship and run; reconsidered if Recall@1 becomes the bottleneck |
| LLM-generated queries (HyDE, query rewriting) | Adds a model call before every search and makes retrieval depend on the LLM; evidence-built queries already carry the error signature |
| LLM-first (let the model choose the cause from retrieved docs) | Confidence would come from text, not data; a poisoned document could steer the diagnosis |

## Consequences

- Measured in CI by the evaluation suite: Recall@1 92 %, Recall@3 100 %, MRR 0.96 on 40 labelled queries; runbook for
  the injected fault in the top 3 for 95 % of simulated incidents.
- The default embedder is lexical; paraphrase-heavy queries benefit from `fastembed` — the benchmark can be re-run with
  either to decide.
- Every passage shown is traceable to a document version (content hash) and section, and the search query is stored.
- Curated tags are a maintenance cost: they are part of the document front matter and reviewed with it.

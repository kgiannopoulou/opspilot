"use client";

import Link from "next/link";
import { useState } from "react";
import { Card, ErrorBox, Spinner } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { query, type KnowledgeDoc, type SearchResults } from "@/services/api";

const SECTIONS: { category: KnowledgeDoc["category"]; title: string; blurb: string }[] = [
  { category: "runbook", title: "Runbooks", blurb: "Step-by-step procedures for known failure modes" },
  { category: "troubleshooting", title: "Troubleshooting", blurb: "Symptom-oriented guides" },
  { category: "architecture", title: "Architecture", blurb: "How ShopStream services work and depend on each other" },
  { category: "process", title: "Process", blurb: "How incidents are run: severities, roles, escalation" },
];

const EXAMPLES = ["checkout freezes after clicking pay", "password reset returns 500", "database connections exhausted"];

function SemanticSearch() {
  const [input, setInput] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);
  const results = useApi<SearchResults>(submitted ? `/knowledge/search${query({ q: submitted, k: 6 })}` : null);
  const run = (q: string) => { setInput(q); if (q.trim().length >= 2) setSubmitted(q.trim()); };

  return (
    <Card title="Semantic search">
      <form onSubmit={(e) => { e.preventDefault(); run(input); }} className="flex gap-2">
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Describe the symptom in customer words…"
          aria-label="Search the knowledge base"
          className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm placeholder:text-slate-600 focus:border-sky-500 focus:outline-none" />
        <button className="rounded-md bg-sky-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-sky-400">Search</button>
      </form>
      {!submitted && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
          Try:
          {EXAMPLES.map((q) => <button key={q} onClick={() => run(q)} className="rounded-full border border-slate-700 px-2 py-0.5 text-slate-300 hover:bg-slate-800">{q}</button>)}
        </div>
      )}
      {submitted && (results.loading && !results.data ? <Spinner label="Searching…" /> : results.error ? <ErrorBox message={results.error} /> : (
        <div className="mt-4">
          <div className="mb-2 text-xs text-slate-500">
            {results.data?.hits.length ?? 0} passages · embedding model <span className="font-mono">{results.data?.embedding_model}</span> · cosine similarity via pgvector (HNSW)
          </div>
          <ol className="space-y-3">
            {results.data?.hits.map((h) => (
              <li key={h.chunk_id} className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
                <div className="flex flex-wrap items-baseline gap-2">
                  <Link href={`/knowledge/${h.slug}`} className="text-sm font-medium text-sky-300 hover:underline">{h.title}</Link>
                  <span className="font-mono text-xs text-slate-400">§{h.section} {h.heading}</span>
                  <span className="ml-auto text-xs tabular-nums text-slate-500" title="Similarity (score after service/tag boosts)">
                    {h.similarity.toFixed(2)}{h.score !== h.similarity && ` → ${h.score.toFixed(2)}`}
                  </span>
                </div>
                <p className="mt-1.5 line-clamp-4 whitespace-pre-line text-xs leading-relaxed text-slate-300">{h.content}</p>
              </li>
            ))}
          </ol>
        </div>
      ))}
    </Card>
  );
}

export default function KnowledgePage() {
  const { data, error, loading } = useApi<KnowledgeDoc[]>("/knowledge");
  if (loading && !data) return <Spinner />;
  if (error) return <ErrorBox message={error} />;
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Knowledge base</h1>
        <p className="text-sm text-slate-400">
          Documents are split into numbered sections, embedded and stored in pgvector; investigations retrieve and cite them.
          Source: <code className="font-mono text-xs">knowledge-base/</code> in the repository.
        </p>
      </div>
      <SemanticSearch />
      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
        {SECTIONS.map((s) => (
          <Card key={s.category} title={s.title}>
            <p className="mb-3 text-xs text-slate-500">{s.blurb}</p>
            <ul className="space-y-3">
              {data?.filter((d) => d.category === s.category).map((d) => (
                <li key={d.slug}>
                  <Link href={`/knowledge/${d.slug}`} className="text-sm font-medium text-sky-300 hover:underline">{d.title}</Link>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {d.service && <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300">{d.service.name}</span>}
                    {d.tags.slice(0, 4).map((t) => <span key={t} className="rounded bg-slate-900 px-1.5 py-0.5 text-[10px] text-slate-500 ring-1 ring-slate-800">{t}</span>)}
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </div>
  );
}

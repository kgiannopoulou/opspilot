"use client";

import Link from "next/link";
import { Card, ErrorBox, Spinner } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import type { KnowledgeDoc } from "@/services/api";

const SECTIONS: { category: KnowledgeDoc["category"]; title: string; blurb: string }[] = [
  { category: "runbook", title: "Runbooks", blurb: "Step-by-step procedures for known failure modes" },
  { category: "troubleshooting", title: "Troubleshooting", blurb: "Symptom-oriented guides" },
  { category: "architecture", title: "Architecture", blurb: "How ShopStream services work and depend on each other" },
];

export default function KnowledgePage() {
  const { data, error, loading } = useApi<KnowledgeDoc[]>("/knowledge");
  if (loading && !data) return <Spinner />;
  if (error) return <ErrorBox message={error} />;
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Knowledge base</h1>
        <p className="text-sm text-slate-400">Documents the investigation engine cites as evidence. Source: <code className="font-mono text-xs">knowledge-base/</code> in the repository.</p>
      </div>
      <div className="grid gap-5 lg:grid-cols-3">
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

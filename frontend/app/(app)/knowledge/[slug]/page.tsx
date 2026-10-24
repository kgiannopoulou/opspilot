"use client";

import Link from "next/link";
import { use } from "react";
import { Markdown } from "@/components/Markdown";
import { Card, ErrorBox, Spinner, utcDateTime } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import type { KnowledgeDoc } from "@/services/api";

export default function KnowledgeDocumentPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const { data, error, loading } = useApi<KnowledgeDoc>(`/knowledge/${slug}`);
  if (loading && !data) return <Spinner />;
  if (error || !data) return <ErrorBox message={error ?? "Not found"} />;
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <Link href="/knowledge" className="text-xs text-slate-400 hover:text-slate-200">← Knowledge base</Link>
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
        <span className="rounded bg-slate-800 px-1.5 py-0.5 uppercase">{data.category}</span>
        {data.service && <span>{data.service.name}</span>}
        <span>· updated {utcDateTime(data.updated_at)}</span>
      </div>
      <Card><Markdown source={data.content ?? ""} /></Card>
    </div>
  );
}

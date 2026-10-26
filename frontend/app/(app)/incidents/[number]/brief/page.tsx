"use client";

import Link from "next/link";
import { use, useState } from "react";
import { ErrorBox, SeverityBadge, Spinner, StatusBadge, relativeTime, utcTime } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import type { Evidence, IncidentDetail } from "@/services/api";
import { statusUpdate } from "@/services/brief";

// One-screen incident brief: what an on-call engineer pastes into the incident channel, or shows a manager.
// Everything on it comes from the latest completed investigation — the same evidence, citations and checks as the
// full incident page, without the tooling around them.

function confidenceLabel(c: number): string {
  return c >= 0.75 ? "High confidence" : c >= 0.5 ? "Medium confidence" : "Low confidence";
}

function EvidenceRow({ e }: { e: Evidence }) {
  const supports = e.stance === "SUPPORTS";
  const contradicts = e.stance === "CONTRADICTS";
  return (
    <li className="flex gap-3 py-1.5">
      <span aria-label={e.stance.toLowerCase()}
        className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${
          supports ? "bg-emerald-500/15 text-emerald-400" : contradicts ? "bg-[#d03b3b]/15 text-[#f08a8a]" : "bg-slate-700/60 text-slate-400"}`}>
        {supports ? "✓" : contradicts ? "✗" : "·"}
      </span>
      <span className="text-[15px] leading-snug text-slate-200">{e.summary}</span>
    </li>
  );
}

export default function IncidentBriefPage({ params }: { params: Promise<{ number: string }> }) {
  const { number } = use(params);
  const incident = useApi<IncidentDetail>(`/incidents/${number}`);
  const [copied, setCopied] = useState(false);

  if (incident.loading && !incident.data) return <Spinner />;
  if (incident.error && !incident.data) return <ErrorBox message={incident.error} />;
  const i = incident.data!;
  const inv = i.latest_investigation?.status === "COMPLETED" ? i.latest_investigation : null;

  const findings = inv ? inv.evidence.filter((e) => e.kind !== "DOCUMENT" && e.stance !== "CONTEXT") : [];
  const runbook = inv?.evidence.find((e) => e.kind === "DOCUMENT" && e.document);
  const query = inv?.queries.find((q) => q.risk === "READ_ONLY");
  const confidence = Math.round((inv?.confidence ?? 0) * 100);
  const citations = inv?.grounding.cited?.length ?? 0;

  async function copy() {
    if (!inv) return;
    await navigator.clipboard.writeText(statusUpdate(i, inv));
    setCopied(true);
    setTimeout(() => setCopied(false), 2_000);
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex items-center justify-between text-xs">
        <Link href={`/incidents/${i.number}`} className="text-slate-400 hover:text-slate-200">← Full investigation</Link>
        {inv && (
          <button onClick={copy} className="rounded-md border border-slate-700 px-3 py-1.5 font-medium text-slate-200 hover:bg-slate-800">
            {copied ? "Copied ✓" : "Copy status update"}
          </button>
        )}
      </div>

      <article id="incident-brief" className="overflow-hidden rounded-2xl border border-slate-700/80 bg-slate-900 shadow-2xl shadow-black/40">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 bg-slate-950/60 px-6 py-4">
          <div className="min-w-0">
            <div className="font-mono text-xs text-slate-500">{i.number} · {i.primary_service?.name ?? "—"} · started {utcTime(i.started_at)} ({relativeTime(i.started_at)})</div>
            <h1 className="mt-0.5 truncate text-xl font-semibold tracking-tight">{i.title}</h1>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge status={i.status} />
            <SeverityBadge severity={i.severity} long />
          </div>
        </header>

        {!inv ? (
          <div className="px-6 py-10 text-center text-sm text-slate-400">
            No completed investigation yet. <Link className="text-sky-400 hover:underline" href={`/incidents/${i.number}`}>Run one</Link> on the incident page.
          </div>
        ) : (
          <div className="space-y-6 px-6 py-5">
            <section>
              <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-sky-400">AI root cause analysis</h2>
              <p className="mt-2 text-lg leading-relaxed text-slate-100">{inv.root_cause}</p>
              <div className="mt-4 flex items-center gap-4">
                <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-slate-800" role="meter" aria-valuenow={confidence} aria-valuemin={0} aria-valuemax={100} aria-label="Confidence">
                  <div className="h-full rounded-full bg-gradient-to-r from-sky-600 to-sky-400" style={{ width: `${confidence}%` }} />
                </div>
                <div className="shrink-0 text-right">
                  <span className="text-2xl font-semibold tabular-nums">{confidence}%</span>
                  <span className="ml-2 text-xs text-slate-400">{confidenceLabel(inv.confidence ?? 0)}</span>
                </div>
              </div>
            </section>

            <section>
              <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">Evidence</h2>
              <ul className="mt-2">{findings.slice(0, 5).map((e) => <EvidenceRow key={e.id} e={e} />)}</ul>
            </section>

            <section>
              <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">Recommended actions</h2>
              <ol className="mt-2 space-y-1.5">
                {inv.recommended_steps.slice(0, 3).map((s, n) => (
                  <li key={n} className="flex gap-3 text-[15px] leading-snug text-slate-200">
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-slate-800 text-[11px] font-semibold text-slate-300">{n + 1}</span>
                    {s}
                  </li>
                ))}
              </ol>
            </section>

            {(runbook || query) && (
              <section className="grid gap-3 sm:grid-cols-2">
                {runbook?.document && (
                  <Link href={`/knowledge/${runbook.document.slug}`} className="rounded-lg border border-slate-800 bg-slate-950/50 p-3 hover:border-slate-700">
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Retrieved runbook</div>
                    <div className="mt-1 text-sm font-medium text-slate-200">{runbook.document.title}</div>
                    <div className="mt-0.5 font-mono text-[11px] text-slate-500">[{runbook.ref}]</div>
                  </Link>
                )}
                {query && (
                  <Link href={`/incidents/${i.number}#queries`} className="rounded-lg border border-slate-800 bg-slate-950/50 p-3 hover:border-slate-700">
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Investigation query · 🔒 read only</div>
                    <div className="mt-1 text-sm font-medium text-slate-200">{query.title}</div>
                    <div className="mt-0.5 truncate font-mono text-[11px] text-slate-500">{query.safe_sql ?? query.sql}</div>
                  </Link>
                )}
              </section>
            )}
          </div>
        )}

        {inv && (
          <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800 bg-slate-950/60 px-6 py-3 text-[11px] text-slate-500">
            <span>{inv.grounding.passed ? `✓ Grounding check passed · ${citations} citations verified` : "Grounding check failed — see the full investigation"}</span>
            <span className="font-mono">{inv.engine} · {inv.duration_ms ?? "—"} ms</span>
          </footer>
        )}
      </article>
    </div>
  );
}

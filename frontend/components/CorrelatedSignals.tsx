"use client";

import Link from "next/link";
import { useState } from "react";
import { Card, ErrorBox, utcTime } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { post, type Correlation, type ServiceSignals } from "@/services/api";

// Status is never colour-alone: each pill carries an icon and the word.
const STATUS: Record<string, { icon: string; className: string }> = {
  anomalous: { icon: "!", className: "bg-[#d03b3b]/15 text-[#f08a8a] ring-[#d03b3b]/40" },
  normal: { icon: "✓", className: "bg-[#0ca30c]/10 text-[#5fd35f] ring-[#0ca30c]/40" },
  "no data": { icon: "?", className: "bg-slate-700/40 text-slate-400 ring-slate-600/50" },
};

function ServiceRow({ s }: { s: ServiceSignals }) {
  const [open, setOpen] = useState(s.status === "anomalous" && s.relation === "reported");
  const st = STATUS[s.status] ?? STATUS["no data"];
  const problems = s.patterns.filter((p) => p.new || p.level === "ERROR");
  return (
    <li className="py-2">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-center gap-2 text-left">
        <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase ring-1 ${st.className}`}>
          {st.icon} {s.status}
        </span>
        <span className="text-sm font-medium text-slate-100">{s.service}</span>
        <span className="text-[10px] uppercase tracking-wide text-slate-500">{s.relation}</span>
        <span className="ml-auto truncate pl-2 text-xs text-slate-400">{s.summary}</span>
      </button>
      {open && (
        <div className="ml-2 mt-2 space-y-1 border-l border-slate-800 pl-3 text-xs">
          {problems.length === 0 && s.patterns.length === 0 && <div className="text-slate-500">No warnings or errors in the window.</div>}
          {(problems.length ? problems : s.patterns).slice(0, 5).map((p) => (
            <div key={p.ref} className="flex items-baseline gap-2">
              <span className={p.level === "ERROR" ? "font-semibold text-[#f08a8a]" : "text-[#fcd27a]"}>{p.level}</span>
              <span className="text-slate-200">{p.count} × {p.message}</span>
              <span className="text-slate-500">{p.new ? (p.baseline_count ? `was ${p.baseline_count} in prior 2 h` : "new") : "usual"}</span>
              <span className="ml-auto font-mono text-[10px] text-slate-500">{p.ref}</span>
            </div>
          ))}
          {s.metrics.map((m) => (
            <div key={m.ref} className="flex items-baseline gap-2">
              <span className={m.anomalous ? "text-[#f08a8a]" : "text-slate-500"}>{m.anomalous ? "▲" : "·"}</span>
              <span className={m.anomalous ? "text-slate-200" : "text-slate-400"}>{m.display}</span>
              <span className="ml-auto font-mono text-[10px] text-slate-500">{m.ref}</span>
            </div>
          ))}
        </div>
      )}
    </li>
  );
}

/** What the platform was doing ±15 minutes around the ticket's creation, and the incident it probably belongs to. */
export function CorrelatedSignals({ number, linkedIncident, canLink, onLinked }: {
  number: string; linkedIncident: string | null | undefined; canLink: boolean; onLinked: () => void;
}) {
  const { data, error, loading, reload } = useApi<Correlation>(`/tickets/${number}/correlation`);
  const [linkError, setLinkError] = useState<string | null>(null);
  if (loading && !data) return <Card title="Correlated signals"><p className="text-sm text-slate-500">Correlating logs and metrics…</p></Card>;
  if (error || !data) return <Card title="Correlated signals"><ErrorBox message={error ?? "Unavailable"} /></Card>;
  const suspect = data.suspected_incident;

  async function link() {
    setLinkError(null);
    try { await post(`/incidents/${suspect!.number}/tickets`, { ticket_number: number }); onLinked(); await reload(); }
    catch (e) { setLinkError(e instanceof Error ? e.message : String(e)); }
  }

  return (
    <Card title="Correlated signals" action={<span className="text-[11px] text-slate-500">{utcTime(data.window_start)}–{utcTime(data.window_end)} · ticket ±15 min</span>}>
      {suspect && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-[#d03b3b]/40 bg-[#d03b3b]/10 px-3 py-2 text-xs">
          <span className="text-slate-200">
            Matches active incident{" "}
            <Link href={`/incidents/${suspect.number}`} className="font-semibold text-sky-300 hover:underline">{suspect.number}</Link>
            {" "}— {suspect.title} ({suspect.services.join(", ")})
          </span>
          {linkedIncident === suspect.number ? (
            <span className="ml-auto text-[#5fd35f]">✓ linked</span>
          ) : canLink && !linkedIncident && (
            <button onClick={link} className="ml-auto rounded-md bg-sky-500 px-2.5 py-1 font-semibold text-slate-950 hover:bg-sky-400">Link ticket</button>
          )}
        </div>
      )}
      {linkError && <div className="mb-2"><ErrorBox message={linkError} /></div>}
      <ul className="divide-y divide-slate-800/70">{data.services.map((s) => <ServiceRow key={s.service} s={s} />)}</ul>
      <p className="mt-2 text-[11px] text-slate-500">
        Scope: the ticket&apos;s service, what it depends on and what depends on it. Patterns are compared with the 2 hours before the window.
      </p>
    </Card>
  );
}

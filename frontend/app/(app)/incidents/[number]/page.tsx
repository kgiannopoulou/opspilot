"use client";

import Link from "next/link";
import { use, useState } from "react";
import { IncidentSeries } from "@/components/charts";
import { InvestigationQueries } from "@/components/InvestigationQueries";
import {
  Card, ErrorBox, PriorityBadge, SeverityBadge, Spinner, StatusBadge, formatDuration, relativeTime, utcDateTime, utcTime,
} from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { useAuth } from "@/hooks/useAuth";
import {
  post, type Evidence, type IncidentDetail, type IncidentStatus, type IncidentTelemetry, type Investigation, type LogLine,
} from "@/services/api";

const STATUS_ACTION: Record<IncidentStatus, string> = {
  INVESTIGATING: "Back to investigating", IDENTIFIED: "Mark identified", MONITORING: "Monitor fix", RESOLVED: "Resolve",
};

const METRIC_LABEL: Record<string, { label: string; format: (v: number) => string }> = {
  api_latency_p95_ms: { label: "p95 latency", format: (v) => (v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`) },
  error_rate: { label: "Error rate", format: (v) => `${(v * 100).toFixed(v < 0.1 ? 1 : 0)}%` },
  query_latency_p95_ms: { label: "Query p95", format: (v) => (v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`) },
  database_connections: { label: "Connections", format: (v) => `${Math.round(v)}` },
};

// Stance is never colour-alone: each row carries an icon and the stance in its tooltip.
const STANCE: Record<Evidence["stance"], { icon: string; className: string; label: string }> = {
  SUPPORTS: { icon: "✓", className: "bg-[#0ca30c]/20 text-[#5fd35f]", label: "Supports the hypothesis" },
  CONTRADICTS: { icon: "✕", className: "bg-[#d03b3b]/20 text-[#f08a8a]", label: "Argues against the hypothesis" },
  CONTEXT: { icon: "•", className: "bg-slate-700/60 text-slate-300", label: "Context" },
};

const KIND_LABEL: Record<Evidence["kind"], string> = {
  LOG_PATTERN: "Logs", METRIC_DELTA: "Metrics", DEPENDENCY_HEALTH: "Dependency", SIMILAR_INCIDENT: "History",
  DOCUMENT: "Docs", CHANGE_EVENT: "Change",
};

function ConfidenceMeter({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const band = pct >= 75 ? "High" : pct >= 45 ? "Medium" : "Low";
  return (
    <div>
      <div className="flex items-baseline gap-2">
        <span className="text-3xl font-semibold tabular-nums">{pct}%</span>
        <span className="text-xs text-slate-400">{band} confidence</span>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-800" role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Confidence">
        <div className="h-full rounded-full bg-sky-400" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

const citationId = (ref: string) => `ev-${ref.replace(/[^A-Za-z0-9-]/g, "_")}`;

function focusEvidence(ref: string) {
  const el = document.getElementById(citationId(ref));
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add("ring-1", "ring-sky-500/70");
  setTimeout(() => el.classList.remove("ring-1", "ring-sky-500/70"), 1600);
}

function Cite({ refs }: { refs: string[] }) {
  return (
    <span className="whitespace-nowrap">
      {refs.map((r) => (
        <button key={r} onClick={() => focusEvidence(r)} title="Show this evidence"
          className="mx-0.5 rounded bg-sky-500/10 px-1 py-px align-baseline font-mono text-[10.5px] text-sky-300 ring-1 ring-sky-500/30 hover:bg-sky-500/20">
          {r}
        </button>
      ))}
    </span>
  );
}

/** Narrative text with its [REF, REF] markers rendered as clickable citations. */
function CitedText({ text }: { text: string }) {
  const parts = text.split(/(\[[A-Z][A-Z]*-[^[\]]+?\])/g);
  return <>{parts.map((p, i) => (/^\[[A-Z]/.test(p) ? <Cite key={i} refs={p.slice(1, -1).split(",").map((r) => r.trim())} /> : p))}</>;
}

function GroundingBadge({ g }: { g: Investigation["grounding"] }) {
  if (g.passed === undefined) return null;
  const rejected = g.rejected_llm_draft;
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px]">
      <span className={`rounded-full px-2 py-0.5 ring-1 ${g.passed ? "bg-[#0ca30c]/10 text-[#5fd35f] ring-[#0ca30c]/40" : "bg-[#d03b3b]/10 text-[#f08a8a] ring-[#d03b3b]/40"}`}>
        {g.passed ? "✓" : "✕"} Grounding check · {g.cited?.length ?? 0} citations verified
      </span>
      {rejected && (
        <span className="text-[#fcd27a]" title={JSON.stringify(rejected, null, 1)}>
          ⚠ LLM draft rejected ({[
            rejected.unknown_refs.length && `${rejected.unknown_refs.length} unknown refs`,
            rejected.uncited_sentences.length && `${rejected.uncited_sentences.length} uncited sentences`,
            rejected.unsupported_numbers.length && `${rejected.unsupported_numbers.length} unsupported numbers`,
          ].filter(Boolean).join(", ")}) — extractive write-up shown
        </span>
      )}
      {g.llm_error && <span className="text-slate-500">LLM unavailable ({g.llm_error}) — extractive write-up shown</span>}
    </div>
  );
}

function EvidenceRow({ e }: { e: Evidence }) {
  const [open, setOpen] = useState(false);
  const s = STANCE[e.stance];
  const sample = (e.data.sample ?? e.data.attributes) as Record<string, unknown> | undefined;
  return (
    <li id={citationId(e.ref)} className="rounded py-2 transition-shadow">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-start gap-3 text-left" aria-expanded={open}>
        <span title={s.label} className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-bold ${s.className}`}>{s.icon}</span>
        <span className="flex-1 text-sm text-slate-200">
          {e.summary}
          {e.related_incident && (
            <Link href={`/incidents/${e.related_incident.number}`} onClick={(ev) => ev.stopPropagation()} className="ml-2 text-xs text-sky-400 hover:underline">
              open {e.related_incident.number} →
            </Link>
          )}
        </span>
        {e.ref && <span className="hidden shrink-0 font-mono text-[10px] text-slate-500 lg:inline">{e.ref}</span>}
        <span className="shrink-0 rounded bg-slate-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-400">{KIND_LABEL[e.kind]}</span>
        {e.weight > 0 && <span className="w-10 shrink-0 text-right text-xs tabular-nums text-slate-500" title="Evidence weight">{e.weight.toFixed(2)}</span>}
      </button>
      {open && (
        <div className="ml-8 mt-2 rounded-md bg-slate-950/70 p-2 font-mono text-[11px] text-slate-400">
          {sample && Object.keys(sample).length > 0 && (
            <div className="mb-1 text-slate-300">sample: {Object.entries(sample).map(([k, v]) => `${k}=${String(v)}`).join(" ")}</div>
          )}
          {Object.entries(e.data).filter(([k]) => k !== "sample" && k !== "attributes").map(([k, v]) => (
            <div key={k}>{k}: {typeof v === "object" ? JSON.stringify(v) : String(v)}</div>
          ))}
        </div>
      )}
    </li>
  );
}

function InvestigationPanel({ incidentNumber, inv, canInvestigate, running, onRun, onAccept }: {
  incidentNumber: string; inv: Investigation | null; canInvestigate: boolean; running: boolean; onRun: () => void;
  onAccept: (rootCause: string) => void;
}) {
  const runButton = canInvestigate && (
    <button onClick={onRun} disabled={running}
      className="rounded-md bg-sky-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-sky-400 disabled:opacity-60">
      {running ? "Investigating…" : inv ? "Re-run investigation" : "Run investigation"}
    </button>
  );

  if (!inv) {
    return (
      <Card title="AI investigation" action={runButton}>
        <div className="py-8 text-center">
          <p className="text-sm text-slate-300">No investigation has been run for this incident yet.</p>
          <p className="mt-1 text-xs text-slate-500">
            The engine correlates logs, metrics, past incidents and runbooks into an evidence-backed root cause.
            {!canInvestigate && " Engineers and admins can start one."}
          </p>
        </div>
      </Card>
    );
  }
  if (inv.status === "FAILED") {
    return <Card title="AI investigation" action={runButton}><ErrorBox message={`Investigation failed: ${inv.error}`} /></Card>;
  }

  const findings = inv.evidence.filter((e) => e.kind !== "DOCUMENT");
  const docs = inv.evidence.filter((e) => e.kind === "DOCUMENT" && e.document);
  const supporting = findings.filter((e) => e.stance === "SUPPORTS").length;
  const byRef = new Map(inv.evidence.map((e) => [e.ref, e]));
  const sources = (inv.grounding.cited ?? []).map((r) => byRef.get(r)).filter((e): e is Evidence => !!e);

  return (
    <Card title="AI investigation" action={runButton}>
      <div className="grid gap-5 md:grid-cols-[1fr_200px]">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Probable root cause</div>
          <p className="mt-1.5 text-base leading-relaxed text-slate-100">{inv.root_cause}</p>
          {inv.narrative && <p className="mt-2 text-sm leading-relaxed text-slate-300"><CitedText text={inv.narrative} /></p>}
          <GroundingBadge g={inv.grounding} />
          {canInvestigate && inv.root_cause && inv.hypothesis_key !== "unknown" && (
            <button onClick={() => onAccept(inv.root_cause!)} className="mt-2 text-xs text-sky-400 hover:underline">
              Accept as root cause → mark identified
            </button>
          )}
        </div>
        <ConfidenceMeter value={inv.confidence ?? 0} />
      </div>

      {sources.length > 0 && (
        <div className="mt-5">
          <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-slate-500">Sources</div>
          <ul className="space-y-1">
            {sources.map((e) => (
              <li key={e.ref} className="flex items-baseline gap-2 text-xs">
                <Cite refs={[e.ref]} />
                <span className="text-slate-400">{KIND_LABEL[e.kind]}</span>
                <span className="truncate text-slate-300">{e.summary}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-6">
        <div className="mb-1 flex items-baseline justify-between">
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Evidence</div>
          <div className="text-xs text-slate-500">{supporting} supporting · {findings.length} findings · click a row for raw data</div>
        </div>
        <ul className="divide-y divide-slate-800/70">{findings.map((e) => <EvidenceRow key={e.id} e={e} />)}</ul>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <div>
          <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Recommended investigation</div>
          <ol className="space-y-2">
            {inv.recommended_steps.map((step, i) => (
              <li key={step} className="flex gap-3 text-sm text-slate-200">
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-800 text-[11px] font-semibold text-slate-300">{i + 1}</span>
                {step}
              </li>
            ))}
          </ol>
        </div>
        <div>
          <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Relevant documentation</div>
          {docs.length === 0 ? <p className="text-sm text-slate-500">No matching documents.</p> : (
            <ul className="space-y-2.5">
              {docs.map((d) => (
                <li key={d.id} id={citationId(d.ref)} className="rounded transition-shadow">
                  <Link href={`/knowledge/${d.document!.slug}`} className="group flex items-center gap-2 text-sm text-sky-300 hover:underline">
                    <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] uppercase text-slate-400 no-underline">{d.document!.category}</span>
                    {d.document!.title}
                    {d.data.section != null && <span className="font-mono text-xs text-slate-400">§{String(d.data.section)}</span>}
                  </Link>
                  {typeof d.data.heading === "string" && (
                    <div className="mt-0.5 text-xs text-slate-500">
                      {d.data.heading}{typeof d.data.similarity === "number" && ` · similarity ${d.data.similarity.toFixed(2)}`}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
          {inv.search_query && (
            <div className="mt-3 text-[11px] text-slate-500" title="Generated from the incident and the matched log signature, then embedded for vector search">
              Retrieved with: <span className="font-mono text-slate-400">{inv.search_query}</span>
            </div>
          )}
        </div>
      </div>

      <InvestigationQueries incident={incidentNumber} investigationId={inv.id} queries={inv.queries} canRun={canInvestigate} />

      <div className="mt-5 border-t border-slate-800 pt-3 text-[11px] text-slate-500">
        Engine <span className="font-mono">{inv.engine}</span> · window {utcTime(inv.window_start)}–{utcTime(inv.window_end)} ·
        {" "}ran {relativeTime(inv.created_at)}{inv.requested_by ? ` by ${inv.requested_by.full_name}` : ""} · {inv.duration_ms} ms ·
        every sentence must cite evidence from this run, or the write-up is rejected
      </div>
    </Card>
  );
}

export default function IncidentPage({ params }: { params: Promise<{ number: string }> }) {
  const { number } = use(params);
  const { can } = useAuth();
  const incident = useApi<IncidentDetail>(`/incidents/${number}`, { pollMs: 30_000 });
  const telemetry = useApi<IncidentTelemetry>(`/incidents/${number}/telemetry`, { pollMs: 60_000 });
  const logs = useApi<LogLine[]>(`/incidents/${number}/logs?limit=25`, { pollMs: 30_000 });
  const [running, setRunning] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [rootCause, setRootCause] = useState("");
  const [error, setError] = useState<string | null>(null);
  const canInvestigate = can("incidents:investigate");

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try { await fn(); await incident.reload(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }
  const runInvestigation = () => act(async () => {
    setRunning(true);
    try { await post(`/incidents/${number}/investigations`); } finally { setRunning(false); }
  });
  const changeStatus = (status: IncidentStatus, root_cause?: string) =>
    act(() => post(`/incidents/${number}/status`, { status, root_cause }));

  if (incident.loading && !incident.data) return <Spinner />;
  if (incident.error && !incident.data) return <ErrorBox message={incident.error} />;
  const i = incident.data!;
  const inv = i.latest_investigation;
  const primary = i.primary_service?.name;
  const charts = (telemetry.data?.series ?? [])
    .filter((s) => METRIC_LABEL[s.metric])
    .sort((a, b) => Number(b.service === primary) - Number(a.service === primary) || a.service.localeCompare(b.service));

  return (
    <div className="space-y-5">
      <Link href="/incidents" className="text-xs text-slate-400 hover:text-slate-200">← Incidents</Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="font-mono text-xs text-slate-500">{i.number}</div>
          <h1 className="text-2xl font-semibold tracking-tight">{i.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <SeverityBadge severity={i.severity} long />
            <StatusBadge status={i.status} />
          </div>
        </div>
        {canInvestigate && (
          <div className="flex flex-wrap gap-2">
            {i.allowed_transitions.map((s) => (
              <button key={s}
                onClick={() => (s === "RESOLVED" ? (setRootCause(i.root_cause ?? inv?.root_cause ?? ""), setResolving(true)) : changeStatus(s))}
                className={`rounded-md px-3 py-1.5 text-xs font-medium ${s === "RESOLVED" ? "bg-emerald-600 text-white hover:bg-emerald-500" : "border border-slate-700 text-slate-200 hover:bg-slate-800"}`}>
                {STATUS_ACTION[s]}
              </button>
            ))}
          </div>
        )}
      </div>

      {error && <ErrorBox message={error} />}
      {resolving && (
        <Card title="Resolve incident">
          <label className="text-xs text-slate-400" htmlFor="root-cause">Root cause (required)</label>
          <textarea id="root-cause" value={rootCause} onChange={(e) => setRootCause(e.target.value)} rows={3}
            className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 p-2 text-sm" />
          <div className="mt-2 flex gap-2">
            <button disabled={!rootCause.trim()} onClick={async () => { await changeStatus("RESOLVED", rootCause); setResolving(false); }}
              className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">Resolve</button>
            <button onClick={() => setResolving(false)} className="rounded-md border border-slate-700 px-3 py-1.5 text-xs">Cancel</button>
          </div>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-sm md:grid-cols-5">
        <div><div className="text-xs text-slate-500">Affected service</div><div className="mt-0.5 font-medium">{primary ?? "—"}</div>
          {i.affected_services.length > 1 && <div className="text-xs text-slate-500">+ {i.affected_services.filter((s) => s.name !== primary).map((s) => s.name).join(", ")}</div>}</div>
        <div><div className="text-xs text-slate-500">Started</div><div className="mt-0.5 font-medium">{utcTime(i.started_at)}</div><div className="text-xs text-slate-500">{relativeTime(i.started_at)}</div></div>
        <div><div className="text-xs text-slate-500">{i.resolved_at ? "Time to resolve" : "Duration"}</div><div className="mt-0.5 font-medium">{formatDuration(i.duration_minutes)}</div></div>
        <div><div className="text-xs text-slate-500">Commander</div><div className="mt-0.5 font-medium">{i.commander?.full_name ?? "Unassigned"}</div></div>
        <div><div className="text-xs text-slate-500">Customer tickets</div><div className="mt-0.5 font-medium">{i.ticket_count}</div></div>
      </div>

      {i.root_cause && (
        <div className="rounded-xl border border-emerald-900/70 bg-emerald-950/30 px-4 py-3 text-sm">
          <span className="text-xs font-semibold uppercase tracking-wider text-emerald-400">Confirmed root cause</span>
          <p className="mt-1 text-slate-200">{i.root_cause}</p>
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-5">
          <InvestigationPanel incidentNumber={i.number} inv={inv} canInvestigate={canInvestigate} running={running} onRun={runInvestigation}
            onAccept={(rc) => changeStatus("IDENTIFIED", rc)} />

          <Card title="Telemetry · affected services">
            {telemetry.loading && !telemetry.data ? <Spinner /> : charts.length === 0 ? (
              <p className="text-sm text-slate-500">No metrics for this window.</p>
            ) : (
              <div className="grid gap-x-6 gap-y-4 md:grid-cols-2">
                {charts.map((s) => (
                  <div key={`${s.service}-${s.metric}`}>
                    <div className="mb-1 text-xs text-slate-400"><span className="text-slate-200">{s.service}</span> · {METRIC_LABEL[s.metric].label}</div>
                    <IncidentSeries points={s.points} onset={i.started_at} label={METRIC_LABEL[s.metric].label} format={METRIC_LABEL[s.metric].format} />
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        <div className="space-y-5">
          {i.summary && <Card title="Summary"><p className="text-sm text-slate-300">{i.summary}</p></Card>}
          <Card title={`Linked tickets · ${i.tickets.length}`}>
            {i.tickets.length === 0 ? <p className="text-sm text-slate-500">No tickets linked.</p> : (
              <ul className="divide-y divide-slate-800/70">
                {i.tickets.map((t) => (
                  <li key={t.number}>
                    <Link href={`/tickets/${t.number}`} className="block py-2 hover:bg-slate-800/30">
                      <div className="flex items-center gap-2"><PriorityBadge priority={t.priority} /><span className="font-mono text-xs text-slate-500">{t.number}</span><span className="ml-auto"><StatusBadge status={t.status} /></span></div>
                      <div className="mt-1 text-sm text-slate-200">{t.title}</div>
                      <div className="text-xs text-slate-500">{t.assignee?.full_name ?? "Unassigned"} · {utcDateTime(t.created_at)}</div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Recent errors & warnings">
            {logs.loading && !logs.data ? <Spinner /> : !logs.data?.length ? <p className="text-sm text-slate-500">No problem logs.</p> : (
              <div className="max-h-[420px] space-y-1 overflow-y-auto font-mono text-[11px] leading-relaxed">
                {logs.data.map((l, n) => (
                  <div key={n} className="break-all">
                    <span className="text-slate-500">{l.ts.slice(11, 19)}</span>{" "}
                    <span className={l.level === "ERROR" ? "font-semibold text-[#f08a8a]" : "text-[#fcd27a]"}>{l.level.padEnd(5)}</span>{" "}
                    <span className="text-sky-300">{l.service}</span>{" "}
                    <span className="text-slate-200">{l.message}</span>{" "}
                    <span className="text-slate-500">{Object.entries(l.attributes).map(([k, v]) => `${k}=${String(v)}`).join(" ")}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

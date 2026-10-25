"use client";

import { useEffect, useState } from "react";
import { ErrorBox, relativeTime } from "@/components/ui";
import { post, type InvestigationQuery, type QueryRun, type SqlVerdict } from "@/services/api";

function RiskBadge({ ok, violations }: { ok: boolean; violations: string[] }) {
  return ok ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-[#0ca30c]/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[#5fd35f] ring-1 ring-[#0ca30c]/40"
      title="Single SELECT on allowlisted tables; runs in a read-only transaction as opspilot_readonly with a 3 s timeout">
      🔒 Read only
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-[#d03b3b]/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[#f08a8a] ring-1 ring-[#d03b3b]/40"
      title={violations.join("\n")}>
      ✕ Blocked
    </span>
  );
}

function ResultTable({ run }: { run: QueryRun }) {
  if (run.status !== "SUCCEEDED") {
    return <div className="mt-2"><ErrorBox message={`${run.status === "REJECTED" ? "Refused by the validator" : "Query failed"}: ${run.error}`} /></div>;
  }
  return (
    <div className="mt-2">
      <div className="max-h-64 overflow-auto rounded-lg border border-slate-800">
        <table className="w-full text-left font-mono text-[11px]">
          <thead className="sticky top-0 bg-slate-900 text-slate-400">
            <tr>{run.columns.map((c) => <th key={c} className="px-2 py-1.5 font-medium">{c}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-slate-800/70 text-slate-200">
            {run.rows.map((row, i) => (
              <tr key={i}>{row.map((v, j) => <td key={j} className="whitespace-nowrap px-2 py-1 tabular-nums">{v === null ? <span className="text-slate-600">null</span> : String(v)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-1 text-[11px] text-slate-500">
        {run.row_count} row{run.row_count === 1 ? "" : "s"}{run.truncated && " (truncated)"} · {run.duration_ms} ms ·
        {" "}{run.executed_by?.full_name ?? "unknown"} {relativeTime(run.executed_at)}{run.edited && " · edited SQL"}
      </div>
    </div>
  );
}

function QueryCard({ q, runUrl, canRun }: { q: InvestigationQuery; runUrl: string; canRun: boolean }) {
  const [sql, setSql] = useState(q.sql);
  const [editing, setEditing] = useState(false);
  const [verdict, setVerdict] = useState<SqlVerdict | null>(null);
  const [run, setRun] = useState<QueryRun | null>(q.last_run);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fromModel = !q.source.startsWith("template:");

  // Live safety check while editing (debounced). The server re-validates on run regardless.
  useEffect(() => {
    if (!editing) return;
    const id = setTimeout(() => { post<SqlVerdict>("/sql/validate", { sql }).then(setVerdict).catch(() => setVerdict(null)); }, 350);
    return () => clearTimeout(id);
  }, [sql, editing]);

  const ok = editing ? verdict?.ok ?? false : q.risk === "READ_ONLY";
  const violations = editing ? verdict?.violations ?? [] : q.violations;

  async function execute() {
    setBusy(true); setError(null);
    try { setRun(await post<QueryRun>(runUrl, editing && sql !== q.sql ? { sql } : {})); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  return (
    <li className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{fromModel ? "AI generated query" : "Suggested query"}</span>
        <RiskBadge ok={ok} violations={violations} />
        <span className="font-mono text-[10px] text-slate-500">{fromModel ? q.source : q.source.replace("template:", "")}</span>
      </div>
      <div className="mt-1.5 text-sm font-medium text-slate-100">{q.title}</div>
      <div className="mt-0.5 text-xs text-slate-400"><span className="text-slate-500">Why: </span>{q.purpose}</div>

      {editing ? (
        <textarea value={sql} onChange={(e) => setSql(e.target.value)} rows={Math.min(12, sql.split("\n").length + 1)} spellCheck={false}
          aria-label="Edit query" className="mt-2 w-full rounded-lg border border-sky-700/60 bg-slate-950 p-2 font-mono text-xs text-emerald-200 focus:outline-none" />
      ) : (
        <pre className="mt-2 overflow-x-auto rounded-lg border border-slate-800 bg-slate-950 p-2.5 font-mono text-xs leading-relaxed text-emerald-200">{q.sql}</pre>
      )}
      {violations.length > 0 && (
        <ul className="mt-1.5 space-y-0.5 text-[11px] text-[#f08a8a]">{violations.map((v) => <li key={v}>✕ {v}</li>)}</ul>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        {canRun && (
          <button onClick={execute} disabled={busy || !ok}
            className="rounded-md bg-sky-500 px-3 py-1 text-xs font-semibold text-slate-950 hover:bg-sky-400 disabled:cursor-not-allowed disabled:opacity-40">
            {busy ? "Running…" : "Run query"}
          </button>
        )}
        {canRun && (
          <button onClick={() => { setEditing((e) => !e); setSql(q.sql); setVerdict(null); }} className="rounded-md border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:bg-slate-800">
            {editing ? "Discard edits" : "Edit"}
          </button>
        )}
        <button onClick={() => navigator.clipboard.writeText(editing ? sql : q.sql)} className="rounded-md px-2 py-1 text-xs text-slate-400 hover:bg-slate-800">Copy</button>
        {!canRun && <span className="text-[11px] text-slate-500">Engineers and admins can run queries.</span>}
      </div>
      {error && <div className="mt-2"><ErrorBox message={error} /></div>}
      {run && <ResultTable run={run} />}
    </li>
  );
}

/** Diagnostic SQL proposed with an investigation. Never executed automatically; every run is validated and audited. */
export function InvestigationQueries({ incident, investigationId, queries, canRun }: {
  incident: string; investigationId: number; queries: InvestigationQuery[]; canRun: boolean;
}) {
  if (queries.length === 0) return null;
  return (
    <div className="mt-6">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Investigation queries</div>
        <div className="text-[11px] text-slate-500">ShopStream DB · read-only sandbox · max 200 rows · 3 s timeout</div>
      </div>
      <ul className="space-y-3">
        {queries.map((q) => (
          <QueryCard key={q.id} q={q} canRun={canRun}
            runUrl={`/incidents/${incident}/investigations/${investigationId}/queries/${q.id}/run`} />
        ))}
      </ul>
    </div>
  );
}

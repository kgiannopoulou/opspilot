"use client";

import { Fragment, useState } from "react";
import { Card, ErrorBox, Pager, Spinner } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { useAuth } from "@/hooks/useAuth";
import { api, download, query, type AuditEvent, type ChainVerification, type Page } from "@/services/api";

const FAMILIES = ["", "ticket.", "incident.", "query.", "ai.", "auth.", "user.", "audit."];

function ChainStatus() {
  const [result, setResult] = useState<ChainVerification | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function verify() {
    setBusy(true); setError(null);
    try { setResult(await api<ChainVerification>("/audit/verify")); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return (
    <Card title="Integrity · hash chain" action={
      <button onClick={verify} disabled={busy} className="rounded-md bg-sky-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-sky-400 disabled:opacity-60">
        {busy ? "Verifying…" : "Verify chain"}
      </button>
    }>
      <p className="text-xs text-slate-400">
        Every row stores <span className="font-mono">sha256(previous hash | row)</span>, computed by a database trigger; updates and
        deletes are refused. Verifying recomputes the whole chain — an edited or removed row breaks it.
      </p>
      {error && <div className="mt-3"><ErrorBox message={error} /></div>}
      {result && (
        <div className="mt-3 text-sm">
          <span className={`rounded-full px-2 py-0.5 text-xs ring-1 ${result.valid ? "bg-[#0ca30c]/10 text-[#5fd35f] ring-[#0ca30c]/40" : "bg-[#d03b3b]/10 text-[#f08a8a] ring-[#d03b3b]/40"}`}>
            {result.valid ? "✓ Chain intact" : `✕ ${result.breaks.length} break(s)`} · {result.rows} rows
          </span>
          {result.head_hash && (
            <div className="mt-2 break-all font-mono text-[11px] text-slate-500" title="Record this value elsewhere to anchor the chain">
              head #{result.head_seq}: {result.head_hash}
            </div>
          )}
          {result.breaks.map((b) => (
            <div key={b.chain_seq} className="mt-1 font-mono text-xs text-[#f08a8a]">#{b.chain_seq} (audit id {b.audit_id}): {b.reason}</div>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function AuditPage() {
  const { can } = useAuth();
  const [action, setAction] = useState("");
  const [entity, setEntity] = useState("");
  const [page, setPage] = useState(1);
  const path = can("audit:read")
    ? `/audit${query({ action, entity_id: entity.trim() || undefined, page, page_size: 50 })}`
    : null;
  const { data, error, loading } = useApi<Page<AuditEvent>>(path);
  const [open, setOpen] = useState<number | null>(null);

  if (!can("audit:read")) return <ErrorBox message="The audit log is available to administrators." />;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Audit log</h1>
          <p className="text-sm text-slate-400">Every important action — by people, the AI and the background worker — append-only and tamper-evident.</p>
        </div>
        <button onClick={() => void download("/audit/export.csv", "opspilot-audit.csv")}
          className="rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800">Export CSV</button>
      </div>

      <ChainStatus />

      <Card title="Events" action={
        <div className="flex gap-2">
          <select aria-label="Action family" value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }}
            className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs">
            {FAMILIES.map((f) => <option key={f} value={f}>{f ? f.slice(0, -1) : "All actions"}</option>)}
          </select>
          <input aria-label="Entity id" placeholder="TCK-… / INC-…" value={entity} onChange={(e) => { setEntity(e.target.value); setPage(1); }}
            className="w-36 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs" />
        </div>
      }>
        {error ? <ErrorBox message={error} /> : loading && !data ? <Spinner /> : (
          <>
            <table className="w-full text-xs">
              <thead><tr className="text-left text-slate-500">
                <th className="pb-2 font-medium">#</th><th className="pb-2 font-medium">When (UTC)</th><th className="pb-2 font-medium">What happened</th>
                <th className="pb-2 font-medium">Action</th><th className="pb-2 font-medium">IP</th><th className="pb-2 font-medium">Hash</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-800/70">
                {data?.items.map((e) => (
                  <Fragment key={e.id}>
                    <tr onClick={() => setOpen(open === e.id ? null : e.id)} className="cursor-pointer hover:bg-slate-800/30">
                      <td className="py-1.5 font-mono text-slate-500">{e.chain_seq}</td>
                      <td className="whitespace-nowrap font-mono text-slate-400">{e.occurred_at.replace("T", " ").slice(0, 19)}</td>
                      <td className="text-slate-200">{e.description}</td>
                      <td className="font-mono text-slate-400">{e.action}</td>
                      <td className="font-mono text-slate-500">{e.ip_address ?? "—"}</td>
                      <td className="font-mono text-slate-500" title={e.hash ?? ""}>{e.hash?.slice(0, 10)}…</td>
                    </tr>
                    {open === e.id && (
                      <tr><td colSpan={6} className="bg-slate-950/70 p-2 font-mono text-[11px] text-slate-400">
                        <div>changes: {JSON.stringify(e.changes)}</div>
                        <div>prev_hash: {e.prev_hash}</div><div>hash: {e.hash}</div>
                      </td></tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
            {data && <Pager page={page} total={data.total} size={50} onChange={setPage} />}
          </>
        )}
      </Card>
    </div>
  );
}

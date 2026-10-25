"use client";

import { useEffect, useState } from "react";
import { Card, ErrorBox, PriorityBadge, relativeTime } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { post, type Classification, type Priority, type ServiceOut, type TicketCategory } from "@/services/api";

const CATEGORIES: TicketCategory[] = ["PAYMENTS", "AUTHENTICATION", "ORDERS", "NOTIFICATIONS", "DATABASE", "PERFORMANCE", "OTHER"];
const PRIORITIES: Priority[] = ["P1", "P2", "P3", "P4"];

const OUTCOME: Record<Classification["status"], { label: string; className: string }> = {
  PENDING: { label: "Awaiting review", className: "text-amber-300 bg-amber-500/10 ring-amber-500/30" },
  ACCEPTED: { label: "Accepted", className: "text-emerald-300 bg-emerald-500/10 ring-emerald-500/30" },
  MODIFIED: { label: "Modified", className: "text-sky-300 bg-sky-500/10 ring-sky-500/30" },
  REJECTED: { label: "Rejected", className: "text-slate-400 bg-slate-500/10 ring-slate-500/30" },
  SUPERSEDED: { label: "Superseded", className: "text-slate-400 bg-slate-500/10 ring-slate-500/30" },
};

export const categoryLabel = (c: string) => c.charAt(0) + c.slice(1).toLowerCase();

function Field({ name, predicted, final }: { name: string; predicted: React.ReactNode; final?: React.ReactNode | null }) {
  return (
    <>
      <dt className="text-slate-500">{name}</dt>
      <dd className="flex items-center gap-2">
        <span className={final ? "text-slate-500 line-through" : ""}>{predicted}</span>
        {final && <><span className="text-slate-500">→</span>{final}</>}
      </dd>
    </>
  );
}

interface Correction { category: TicketCategory; priority: Priority; service: string; note: string }

/** AI triage suggestion for a ticket. Nothing is applied until an analyst accepts or corrects it. */
export function TriageSuggestion({ number, createdAt, canWrite, onChanged }: {
  number: string; createdAt: string; canWrite: boolean; onChanged: () => void;
}) {
  // A new ticket is triaged by the background worker: poll briefly until its suggestion appears.
  const [waiting, setWaiting] = useState(() => Date.now() - new Date(createdAt).getTime() < 120_000);
  const suggestion = useApi<Classification | null>(`/tickets/${number}/classification`, { pollMs: waiting ? 1_500 : undefined });
  if (waiting && suggestion.data) setWaiting(false);
  useEffect(() => {
    if (!waiting) return;
    const id = setTimeout(() => setWaiting(false), Math.max(0, 120_000 - (Date.now() - new Date(createdAt).getTime())));
    return () => clearTimeout(id);
  }, [waiting, createdAt]);
  const services = useApi<ServiceOut[]>(canWrite ? "/services" : null);
  const [form, setForm] = useState<Correction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const c = suggestion.data;

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try { await fn(); setForm(null); await suggestion.reload(); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  const review = (body: object) => act(() => post(`/tickets/${number}/classification/${c!.id}/review`, body));
  const rerun = canWrite && (
    <button onClick={() => act(() => post(`/tickets/${number}/classification`))} disabled={busy}
      className="text-xs text-sky-400 hover:underline disabled:opacity-50">{c ? "Re-run" : "Classify"}</button>
  );

  if (!c) {
    return (
      <Card title="AI triage" action={rerun}>
        {waiting ? (
          <div>
            <div className="text-sm text-slate-300">AI triage running in the background…</div>
            <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
              <div className="h-full w-1/3 animate-pulse rounded-full bg-sky-400" />
            </div>
            <p className="mt-2 text-[11px] text-slate-500">Correlating logs and metrics, then classifying. The ticket was saved immediately.</p>
          </div>
        ) : <p className="text-sm text-slate-500">{suggestion.loading ? "Loading…" : "No suggestion for this ticket yet."}</p>}
      </Card>
    );
  }
  const pending = c.status === "PENDING";
  const outcome = OUTCOME[c.status];
  const differs = (a: unknown, b: unknown) => b !== null && b !== undefined && a !== b;

  return (
    <Card title="AI triage suggestion" action={rerun}>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <span className={`rounded-full px-2 py-0.5 ring-1 ${outcome.className}`}>{outcome.label}</span>
        <span className="text-slate-400">{Math.round(c.confidence * 100)}% confidence</span>
        <span className="font-mono text-[10px] text-slate-500">{c.engine}</span>
      </div>

      {!form ? (
        <dl className="grid grid-cols-[80px_1fr] gap-y-1.5 text-sm">
          <Field name="Category" predicted={categoryLabel(c.category)}
            final={differs(c.category, c.final_category) ? <span>{categoryLabel(c.final_category!)}</span> : null} />
          <Field name="Priority" predicted={<PriorityBadge priority={c.priority} />}
            final={differs(c.priority, c.final_priority) ? <PriorityBadge priority={c.final_priority!} /> : null} />
          <Field name="Service" predicted={c.service?.name ?? "unknown"}
            final={c.status === "MODIFIED" && differs(c.service?.name, c.final_service?.name) ? <span>{c.final_service!.name}</span> : null} />
        </dl>
      ) : (
        <div className="space-y-2 text-sm">
          <label className="flex items-center gap-2">
            <span className="w-16 text-slate-500">Category</span>
            <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value as TicketCategory })}
              className="flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-1">
              {CATEGORIES.map((o) => <option key={o} value={o}>{categoryLabel(o)}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span className="w-16 text-slate-500">Priority</span>
            <select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value as Priority })}
              className="flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-1">
              {PRIORITIES.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span className="w-16 text-slate-500">Service</span>
            <select value={form.service} onChange={(e) => setForm({ ...form, service: e.target.value })}
              className="flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-1">
              {services.data?.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </label>
          <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="Why? (optional)"
            className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1" />
        </div>
      )}

      <p className="mt-3 text-xs leading-relaxed text-slate-400">{c.rationale}</p>
      {error && <div className="mt-2"><ErrorBox message={error} /></div>}

      {pending && canWrite && (
        <div className="mt-3 flex flex-wrap gap-2">
          {!form ? (
            <>
              <button disabled={busy} onClick={() => review({ action: "accept" })}
                className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50">Accept</button>
              <button disabled={busy}
                onClick={() => setForm({ category: c.category, priority: c.priority, service: c.service?.name ?? services.data?.[0]?.name ?? "", note: "" })}
                className="rounded-md border border-slate-700 px-3 py-1.5 text-xs hover:bg-slate-800">Modify</button>
              <button disabled={busy} onClick={() => review({ action: "reject" })}
                className="rounded-md px-3 py-1.5 text-xs text-slate-400 hover:bg-slate-800">Reject</button>
            </>
          ) : (
            <>
              <button disabled={busy} onClick={() => review({ action: "modify", ...form, note: form.note || null })}
                className="rounded-md bg-sky-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-sky-400 disabled:opacity-50">Apply corrected values</button>
              <button onClick={() => setForm(null)} className="rounded-md border border-slate-700 px-3 py-1.5 text-xs">Cancel</button>
            </>
          )}
        </div>
      )}
      {!pending && c.reviewed_by && (
        <p className="mt-3 border-t border-slate-800 pt-2 text-[11px] text-slate-500">
          {outcome.label} by {c.reviewed_by.full_name} {c.reviewed_at && relativeTime(c.reviewed_at)}{c.review_note && ` — “${c.review_note}”`}
        </p>
      )}
      {pending && <p className="mt-3 text-[11px] text-slate-500">Suggestions are never applied automatically.</p>}
    </Card>
  );
}

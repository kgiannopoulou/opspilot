"use client";

import Link from "next/link";
import { use, useState } from "react";
import { Card, ErrorBox, PriorityBadge, Spinner, StatusBadge, relativeTime, utcDateTime } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { useAuth } from "@/hooks/useAuth";
import { post, type Ticket, type TicketComment, type TicketEvent, type TicketStatus, type UserRef } from "@/services/api";

const ACTION_LABEL: Record<TicketStatus, string> = {
  OPEN: "Back to open", TRIAGED: "Triage", INVESTIGATING: "Start investigating", RESOLVED: "Resolve", CLOSED: "Close",
};

function describe(e: TicketEvent): string {
  switch (e.type) {
    case "CREATED": return "created the ticket";
    case "STATUS_CHANGED": return `moved ${e.from_value} → ${e.to_value}`;
    case "PRIORITY_CHANGED": return `changed priority ${e.from_value} → ${e.to_value}`;
    case "ASSIGNED": return e.to_value ? `assigned to ${e.to_value}` : "unassigned the ticket";
    case "COMMENTED": return "commented";
    case "LINKED_TO_INCIDENT": return `linked to incident ${e.to_value}`;
    default: return e.type.toLowerCase().replaceAll("_", " ");
  }
}

export default function TicketPage({ params }: { params: Promise<{ number: string }> }) {
  const { number } = use(params);
  const { can } = useAuth();
  const ticket = useApi<Ticket>(`/tickets/${number}`);
  const comments = useApi<TicketComment[]>(`/tickets/${number}/comments`);
  const events = useApi<TicketEvent[]>(`/tickets/${number}/events`);
  const users = useApi<UserRef[]>("/users");
  const [body, setBody] = useState("");
  const [internal, setInternal] = useState(true);
  const [resolution, setResolution] = useState("");
  const [error, setError] = useState<string | null>(null);
  const canWrite = can("tickets:write");

  const refresh = () => { void ticket.reload(); void comments.reload(); void events.reload(); };
  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try { await fn(); refresh(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }

  if (ticket.loading && !ticket.data) return <Spinner />;
  if (ticket.error) return <ErrorBox message={ticket.error} />;
  const t = ticket.data!;
  const linkedIncident = events.data?.find((e) => e.type === "LINKED_TO_INCIDENT")?.to_value;

  return (
    <div className="space-y-4">
      <Link href="/tickets" className="text-xs text-slate-400 hover:text-slate-200">← Tickets</Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="font-mono text-xs text-slate-500">{t.number}</div>
          <h1 className="text-xl font-semibold">{t.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <PriorityBadge priority={t.priority} /><StatusBadge status={t.status} />
            {t.labels.map((l) => <span key={l} className="rounded bg-slate-800 px-1.5 py-0.5 text-[11px] text-slate-300">{l}</span>)}
            {linkedIncident && (
              <Link href={`/incidents/${linkedIncident}`} className="rounded bg-red-500/10 px-1.5 py-0.5 text-[11px] text-red-300 ring-1 ring-red-500/30">
                Incident {linkedIncident}
              </Link>
            )}
          </div>
        </div>
        {canWrite && t.allowed_transitions.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            {t.allowed_transitions.some((s) => s === "RESOLVED" || (t.status === "OPEN" && s === "CLOSED")) && (
              <input value={resolution} onChange={(e) => setResolution(e.target.value)} placeholder="Resolution note"
                className="w-56 rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm" />
            )}
            {t.allowed_transitions.map((s) => (
              <button key={s} onClick={() => act(() => post(`/tickets/${t.number}/transition`, { status: s, resolution: resolution || null }))}
                className="rounded-md border border-slate-700 px-3 py-1.5 text-sm hover:bg-slate-800">
                {ACTION_LABEL[s]}
              </button>
            ))}
          </div>
        )}
      </div>
      {error && <ErrorBox message={error} />}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card title="Description"><p className="whitespace-pre-wrap text-sm text-slate-200">{t.description}</p>
            {t.resolution && <p className="mt-3 rounded-md bg-emerald-500/10 p-3 text-sm text-emerald-200"><b>Resolution:</b> {t.resolution}</p>}
          </Card>
          <Card title={`Comments (${comments.data?.length ?? 0})`}>
            <ul className="space-y-3">
              {comments.data?.map((c) => (
                <li key={c.id} className={`rounded-lg border p-3 text-sm ${c.internal ? "border-amber-900/60 bg-amber-950/20" : "border-slate-800"}`}>
                  <div className="mb-1 flex justify-between text-xs text-slate-400">
                    <span><b className="text-slate-200">{c.author.full_name}</b>{c.internal && " · internal note"}</span>
                    <span>{relativeTime(c.created_at)}</span>
                  </div>
                  <p className="whitespace-pre-wrap">{c.body}</p>
                </li>
              ))}
            </ul>
            {canWrite && (
              <form className="mt-3 space-y-2" onSubmit={(e) => {
                e.preventDefault();
                if (body.trim()) void act(async () => { await post(`/tickets/${t.number}/comments`, { body, internal }); setBody(""); });
              }}>
                <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={3} placeholder="Add a comment…"
                  className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
                <div className="flex items-center justify-between">
                  <label className="flex items-center gap-2 text-xs text-slate-400">
                    <input type="checkbox" checked={internal} onChange={(e) => setInternal(e.target.checked)} /> Internal note
                  </label>
                  <button className="rounded-md bg-sky-500 px-3 py-1.5 text-sm font-semibold text-slate-950">Comment</button>
                </div>
              </form>
            )}
          </Card>
        </div>
        <div className="space-y-4">
          <Card title="Details">
            <dl className="grid grid-cols-[110px_1fr] gap-y-2 text-sm">
              <dt className="text-slate-500">Service</dt><dd>{t.service?.name ?? "—"}</dd>
              <dt className="text-slate-500">Reporter</dt><dd>{t.reporter.full_name}</dd>
              <dt className="text-slate-500">Assignee</dt>
              <dd>
                {canWrite ? (
                  <select value={t.assignee?.id ?? ""} className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm"
                    onChange={(e) => act(() => post(`/tickets/${t.number}/assign`, { assignee_id: e.target.value ? Number(e.target.value) : null }))}>
                    <option value="">Unassigned</option>
                    {users.data?.map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
                  </select>
                ) : (t.assignee?.full_name ?? "Unassigned")}
              </dd>
              <dt className="text-slate-500">Source</dt><dd>{t.source.replace("_", " ")}</dd>
              <dt className="text-slate-500">Created</dt><dd>{utcDateTime(t.created_at)}</dd>
              {t.resolved_at && (<><dt className="text-slate-500">Resolved</dt><dd>{utcDateTime(t.resolved_at)}</dd></>)}
            </dl>
          </Card>
          <Card title="Timeline">
            <ol className="relative space-y-3 border-l border-slate-800 pl-4 text-sm">
              {events.data?.map((e) => (
                <li key={e.id}>
                  <span className="absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full bg-slate-600" />
                  <div><b className="text-slate-200">{e.actor?.full_name ?? "System"}</b> <span className="text-slate-400">{describe(e)}</span></div>
                  <div className="text-xs text-slate-500">{utcDateTime(e.created_at)}</div>
                </li>
              ))}
            </ol>
          </Card>
        </div>
      </div>
    </div>
  );
}

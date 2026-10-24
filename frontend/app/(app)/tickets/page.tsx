"use client";

import Link from "next/link";
import { useState } from "react";
import { Card, ErrorBox, Pager, PriorityBadge, Spinner, StatusBadge, relativeTime } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { useAuth } from "@/hooks/useAuth";
import { post, query, type Page, type Priority, type ServiceOut, type Ticket } from "@/services/api";

const OPEN = ["OPEN", "TRIAGED", "INVESTIGATING"];
const VIEWS = { open: OPEN, resolved: ["RESOLVED"], closed: ["CLOSED"], all: [] as string[] };

export default function TicketsPage() {
  const { can } = useAuth();
  const [view, setView] = useState<keyof typeof VIEWS>("open");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const { data, error, loading, reload } = useApi<Page<Ticket>>(
    `/tickets${query({ status: VIEWS[view], q, page, page_size: 25 })}`,
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Tickets</h1>
        <div className="flex flex-wrap items-center gap-2">
          <input value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} placeholder="Search title, number…"
            className="w-56 rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm outline-none focus:border-sky-500" />
          <div className="flex rounded-lg border border-slate-800 p-0.5 text-sm">
            {(Object.keys(VIEWS) as (keyof typeof VIEWS)[]).map((v) => (
              <button key={v} onClick={() => { setView(v); setPage(1); }}
                className={`rounded-md px-3 py-1 capitalize ${view === v ? "bg-slate-800 text-slate-100" : "text-slate-400"}`}>{v}</button>
            ))}
          </div>
          {can("tickets:write") && (
            <button onClick={() => setCreating(true)} className="rounded-md bg-sky-500 px-3 py-1.5 text-sm font-semibold text-slate-950 hover:bg-sky-400">
              New ticket
            </button>
          )}
        </div>
      </div>
      {creating && <NewTicket onClose={() => setCreating(false)} onCreated={() => { setCreating(false); void reload(); }} />}
      {error && <ErrorBox message={error} />}
      <Card>
        {loading && !data ? <Spinner /> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-500">
                  <th className="pb-2 font-medium">Ticket</th><th className="pb-2 font-medium">Priority</th>
                  <th className="pb-2 font-medium">Status</th><th className="pb-2 font-medium">Service</th>
                  <th className="pb-2 font-medium">Assignee</th><th className="pb-2 text-right font-medium">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {data?.items.map((t) => (
                  <tr key={t.id} className="hover:bg-slate-800/30">
                    <td className="py-2.5">
                      <Link href={`/tickets/${t.number}`}>
                        <span className="font-mono text-xs text-slate-500">{t.number}</span>
                        <div className="font-medium hover:text-sky-300">{t.title}</div>
                      </Link>
                    </td>
                    <td><PriorityBadge priority={t.priority} /></td>
                    <td><StatusBadge status={t.status} /></td>
                    <td className="text-xs text-slate-400">{t.service?.name ?? "—"}</td>
                    <td className="text-slate-300">{t.assignee?.full_name ?? <span className="text-slate-500">Unassigned</span>}</td>
                    <td className="text-right text-xs text-slate-400">{relativeTime(t.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data && <Pager page={page} total={data.total} size={data.page_size} onChange={setPage} />}
          </div>
        )}
      </Card>
    </div>
  );
}

function NewTicket({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const { data: services } = useApi<ServiceOut[]>("/services");
  const [form, setForm] = useState({ title: "", description: "", priority: "P3" as Priority, service: "" });
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await post("/tickets", { ...form, service: form.service || null });
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const input = "w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-sky-500";
  return (
    <Card title="New ticket">
      <form onSubmit={submit} className="grid gap-3 md:grid-cols-4">
        <input required minLength={3} placeholder="Title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className={`${input} md:col-span-2`} />
        <select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value as Priority })} className={input}>
          {["P1", "P2", "P3", "P4"].map((p) => <option key={p}>{p}</option>)}
        </select>
        <select value={form.service} onChange={(e) => setForm({ ...form, service: e.target.value })} className={input}>
          <option value="">No service</option>
          {services?.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
        </select>
        <textarea required placeholder="What is the customer seeing?" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className={`${input} md:col-span-4`} rows={3} />
        {error && <p className="text-sm text-red-300 md:col-span-4">{error}</p>}
        <div className="flex gap-2 md:col-span-4">
          <button className="rounded-md bg-sky-500 px-3 py-1.5 text-sm font-semibold text-slate-950">Create</button>
          <button type="button" onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-slate-400">Cancel</button>
        </div>
      </form>
    </Card>
  );
}

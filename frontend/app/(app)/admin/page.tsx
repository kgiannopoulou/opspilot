"use client";

import { useState } from "react";
import { Card, ErrorBox, Spinner, relativeTime } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { useAuth } from "@/hooks/useAuth";
import { del, patch, type AdminUser, type BackgroundJob, type JobsOverview } from "@/services/api";

const ROLES = ["admin", "engineer", "support_agent", "viewer"];

const JOB_STYLE: Record<BackgroundJob["status"], string> = {
  QUEUED: "text-slate-300", RUNNING: "text-sky-300", SUCCEEDED: "text-[#5fd35f]", RETRYING: "text-[#fcd27a]", DEAD: "text-[#f08a8a]",
};

function BackgroundJobs() {
  const { data, error } = useApi<JobsOverview>("/jobs?limit=15", { pollMs: 5_000 });
  if (error) return <ErrorBox message={error} />;
  if (!data) return <Spinner />;
  const q = data.queue;
  const tiles: [string, number | string][] = q.available
    ? [["Waiting", q.waiting], ["In progress", q.in_progress], ["Retry scheduled", q.delayed], ["Dead letters", q.dead]]
    : [["Redis", "unavailable — jobs run in-process"]];
  return (
    <Card title="Background jobs · Redis Streams" action={<span className="text-[11px] text-slate-500">refreshes every 5 s</span>}>
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        {tiles.map(([label, value]) => (
          <div key={label} className="rounded-lg border border-slate-800 px-3 py-2">
            <div className="text-[11px] text-slate-500">{label}</div>
            <div className="text-lg font-semibold tabular-nums">{value}</div>
          </div>
        ))}
      </div>
      <table className="w-full text-xs">
        <thead><tr className="text-left text-slate-500">
          <th className="pb-2 font-medium">Job</th><th className="pb-2 font-medium">For</th><th className="pb-2 font-medium">Status</th>
          <th className="pb-2 font-medium">Attempts</th><th className="pb-2 font-medium">Worker</th><th className="pb-2 font-medium">Took</th><th className="pb-2 font-medium">Enqueued</th>
        </tr></thead>
        <tbody className="divide-y divide-slate-800/70">
          {data.recent.map((j) => (
            <tr key={j.id} title={j.error ?? undefined}>
              <td className="py-1.5 font-mono">{j.type}</td>
              <td>{j.entity_type} {j.entity_id}</td>
              <td className={`font-semibold ${JOB_STYLE[j.status]}`}>{j.status}</td>
              <td className="tabular-nums">{j.attempts}</td>
              <td className="font-mono text-slate-400">{j.worker ?? "—"}</td>
              <td className="tabular-nums text-slate-400">{j.started_at && j.finished_at ? `${new Date(j.finished_at).getTime() - new Date(j.started_at).getTime()} ms` : "—"}</td>
              <td className="text-slate-400">{relativeTime(j.enqueued_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.recent.length === 0 && <p className="text-sm text-slate-500">No jobs yet. Create a ticket or run an investigation.</p>}
    </Card>
  );
}

export default function AdminPage() {
  const { user, can } = useAuth();
  const { data, error, loading, reload } = useApi<AdminUser[]>(can("users:manage") ? "/users/admin" : null);
  const [actionError, setActionError] = useState<string | null>(null);

  if (!can("users:manage")) return <ErrorBox message="You need the admin role to manage users." />;
  if (loading && !data) return <Spinner />;

  async function run(fn: () => Promise<unknown>) {
    setActionError(null);
    try { await fn(); await reload(); } catch (e) { setActionError(e instanceof Error ? e.message : String(e)); }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Administration</h1>
      <BackgroundJobs />
      <h2 className="pt-2 text-sm font-semibold uppercase tracking-wider text-slate-400">Users</h2>
      {(error || actionError) && <ErrorBox message={(error || actionError)!} />}
      <Card>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-500">
              <th className="pb-2 font-medium">User</th><th className="pb-2 font-medium">Role</th>
              <th className="pb-2 font-medium">Status</th><th className="pb-2 font-medium">Last login</th><th />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/70">
            {data?.map((u) => (
              <tr key={u.id}>
                <td className="py-2.5"><div className="font-medium">{u.full_name}</div><div className="text-xs text-slate-500">{u.email}</div></td>
                <td>
                  <select value={u.role} disabled={u.id === user?.id || !u.is_active}
                    onChange={(e) => run(() => patch(`/users/${u.id}`, { role: e.target.value }))}
                    className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm disabled:opacity-50">
                    {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                </td>
                <td>{u.is_active ? <span className="text-emerald-300">Active</span> : <span className="text-slate-500">Deactivated</span>}</td>
                <td className="text-xs text-slate-400">{u.last_login_at ? relativeTime(u.last_login_at) : "never"}</td>
                <td className="text-right">
                  {u.is_active && u.id !== user?.id && (
                    <button onClick={() => run(() => del(`/users/${u.id}`))} className="rounded px-2 py-1 text-xs text-red-300 hover:bg-red-950/50">
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

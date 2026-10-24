"use client";

import { useState } from "react";
import { Card, ErrorBox, Spinner, relativeTime } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { useAuth } from "@/hooks/useAuth";
import { del, patch, type AdminUser } from "@/services/api";

const ROLES = ["admin", "engineer", "support_agent", "viewer"];

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
      <h1 className="text-xl font-semibold">User management</h1>
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

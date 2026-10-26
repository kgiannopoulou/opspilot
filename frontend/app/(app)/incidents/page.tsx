"use client";

import Link from "next/link";
import { useState } from "react";
import { Card, ErrorBox, Pager, SeverityBadge, Spinner, StatusBadge, formatDuration, utcDateTime } from "@/components/ui";
import { DeclareIncident } from "@/components/DeclareIncident";
import { useApi } from "@/hooks/useApi";
import { useAuth } from "@/hooks/useAuth";
import { query, type IncidentSummary, type Page } from "@/services/api";

const STATES = [["active", "Active"], ["resolved", "Resolved"], ["all", "All"]] as const;

export default function IncidentsPage() {
  const [state, setState] = useState<"active" | "resolved" | "all">("active");
  const [page, setPage] = useState(1);
  const [declaring, setDeclaring] = useState(false);
  const { can } = useAuth();
  const { data, error, loading } = useApi<Page<IncidentSummary>>(
    `/incidents${query({ state, page, page_size: 20 })}`, { pollMs: 30_000 },
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Incidents</h1>
        <div className="flex items-center gap-3">
        {can("incidents:investigate") && !declaring && (
          <button onClick={() => setDeclaring(true)} className="rounded-md bg-sky-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-sky-400">
            Declare incident
          </button>
        )}
        <div className="flex rounded-lg border border-slate-800 p-0.5 text-sm">
          {STATES.map(([value, label]) => (
            <button key={value} onClick={() => { setState(value); setPage(1); }}
              className={`rounded-md px-3 py-1 ${state === value ? "bg-slate-800 text-slate-100" : "text-slate-400 hover:text-slate-200"}`}>
              {label}
            </button>
          ))}
        </div>
        </div>
      </div>
      {declaring && <DeclareIncident onCancel={() => setDeclaring(false)} />}
      {error && <ErrorBox message={error} />}
      <Card>
        {loading && !data ? <Spinner /> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[800px] text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-500">
                  <th className="pb-2 font-medium">Incident</th><th className="pb-2 font-medium">Severity</th>
                  <th className="pb-2 font-medium">Status</th><th className="pb-2 font-medium">Services</th>
                  <th className="pb-2 font-medium">Started</th><th className="pb-2 text-right font-medium">Duration</th>
                  <th className="pb-2 text-right font-medium">Tickets</th><th className="pb-2 pl-4 font-medium">Commander</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {data?.items.map((i) => (
                  <tr key={i.id} className="hover:bg-slate-800/30">
                    <td className="py-2.5">
                      <Link href={`/incidents/${i.number}`} className="block">
                        <span className="font-mono text-xs text-slate-500">{i.number}</span>
                        <div className="font-medium text-slate-100 hover:text-sky-300">{i.title}</div>
                      </Link>
                    </td>
                    <td><SeverityBadge severity={i.severity} /></td>
                    <td><StatusBadge status={i.status} /></td>
                    <td className="text-xs text-slate-400">{i.affected_services.map((s) => s.name).join(", ")}</td>
                    <td className="text-xs text-slate-400">{utcDateTime(i.started_at)}</td>
                    <td className="text-right tabular-nums text-slate-300">{formatDuration(i.duration_minutes)}</td>
                    <td className="text-right tabular-nums text-slate-400">{i.ticket_count}</td>
                    <td className="pl-4 text-slate-400">{i.commander?.full_name ?? "—"}</td>
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

"use client";

import Link from "next/link";
import { SeverityStackedBars, Sparkline, VolumeArea } from "@/components/charts";
import {
  Card, ErrorBox, HealthPill, SeverityBadge, Spinner, StatusBadge, formatDuration, relativeTime,
} from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import type { DashboardSummary, Severity } from "@/services/api";

const TILES: { severity: Severity; label: string; accent: string }[] = [
  { severity: "SEV1", label: "SEV1 · Critical", accent: "border-t-[#d03b3b]" },
  { severity: "SEV2", label: "SEV2 · Major", accent: "border-t-[#ec835a]" },
  { severity: "SEV3", label: "SEV3 · Minor", accent: "border-t-[#fab219]" },
];

function StatTile({ label, value, sub, accent = "border-t-slate-700" }: {
  label: string; value: React.ReactNode; sub?: React.ReactNode; accent?: string;
}) {
  return (
    <div className={`rounded-xl border border-slate-800 border-t-2 bg-slate-900/60 px-4 py-3 ${accent}`}>
      <div className="text-xs text-slate-400">{label}</div>
      <div className="mt-1 text-3xl font-semibold tracking-tight">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

function MttrDelta({ current, previous }: { current: number | null; previous: number | null }) {
  if (current === null || previous === null || previous === 0) return <>last 30 days</>;
  const change = ((current - previous) / previous) * 100;
  const better = change < 0;
  return (
    <span className={better ? "text-[#0ca30c]" : "text-[#ec835a]"}>
      {better ? "▼" : "▲"} {Math.abs(change).toFixed(0)}% vs previous 30 days
    </span>
  );
}

export default function DashboardPage() {
  const { data, error, loading } = useApi<DashboardSummary>("/dashboard/summary", { pollMs: 30_000 });

  if (loading && !data) return <Spinner />;
  if (error && !data) return <ErrorBox message={error} />;
  if (!data) return null;

  const activeTotal = Object.values(data.active_incidents).reduce((a, b) => a + b, 0);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Operations overview</h1>
          <p className="text-sm text-slate-400">
            {activeTotal > 0 ? `${activeTotal} active incident${activeTotal > 1 ? "s" : ""}` : "All systems operational"}
            {" · "}updated {relativeTime(data.generated_at)}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        {TILES.map((t) => (
          <StatTile key={t.severity} label={t.label} value={data.active_incidents[t.severity]} sub="active" accent={t.accent} />
        ))}
        <StatTile label="Resolved" value={data.resolved_last_30d} sub="incidents, last 30 days" accent="border-t-[#0ca30c]" />
        <StatTile label="MTTR" value={formatDuration(data.mttr_minutes)}
          sub={<MttrDelta current={data.mttr_minutes} previous={data.mttr_minutes_previous} />} />
        <StatTile label="Open tickets" value={data.tickets.open}
          sub={`${data.tickets.unassigned} unassigned · ${data.tickets.created_today} today`} />
      </div>

      <Card title="Active incidents" action={<Link href="/incidents" className="text-xs text-sky-400 hover:underline">All incidents →</Link>}>
        {data.active.length === 0 ? (
          <p className="text-sm text-slate-400">No active incidents.</p>
        ) : (
          <ul className="divide-y divide-slate-800">
            {data.active.map((i) => (
              <li key={i.id}>
                <Link href={`/incidents/${i.number}`} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-3 hover:bg-slate-800/30">
                  <SeverityBadge severity={i.severity} />
                  <span className="w-16 font-mono text-xs text-slate-400">{i.number}</span>
                  <span className="min-w-48 flex-1 font-medium">{i.title}</span>
                  <span className="text-xs text-slate-400">{i.affected_services.map((s) => s.name).join(", ")}</span>
                  <span className="text-xs text-slate-500">{i.ticket_count} ticket{i.ticket_count === 1 ? "" : "s"}</span>
                  <span className="w-24 text-right text-xs text-slate-400">{formatDuration(i.duration_minutes)}</span>
                  <StatusBadge status={i.status} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Incident volume · 30 days">
          <SeverityStackedBars data={data.incident_trend} />
        </Card>
        <Card title="Ticket volume · 30 days">
          <VolumeArea data={data.ticket_volume} label="Tickets created" />
        </Card>
      </div>

      <Card title="Service health · last 6 hours" action={<Link href="/services" className="text-xs text-sky-400 hover:underline">Services →</Link>}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-500">
                <th className="pb-2 font-medium">Service</th>
                <th className="pb-2 font-medium">Status</th>
                <th className="pb-2 text-right font-medium">Error rate</th>
                <th className="pb-2 pl-6 font-medium">Error rate trend</th>
                <th className="pb-2 text-right font-medium">p95 latency</th>
                <th className="pb-2 text-right font-medium">vs. baseline</th>
                <th className="pb-2 text-right font-medium">Incidents</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              {data.services.map((s) => {
                const ratio = s.latency_p95_ms && s.latency_baseline_ms ? s.latency_p95_ms / s.latency_baseline_ms : null;
                return (
                  <tr key={s.name}>
                    <td className="py-2.5">
                      <div className="font-medium">{s.name}</div>
                      <div className="text-xs text-slate-500">Tier {s.tier} · {s.owner_team}</div>
                    </td>
                    <td><HealthPill status={s.status} /></td>
                    <td className="text-right tabular-nums">{s.error_rate === null ? "—" : `${(s.error_rate * 100).toFixed(1)}%`}</td>
                    <td className="pl-6"><Sparkline values={s.error_rate_series.map((p) => p.error_rate)} /></td>
                    <td className="text-right tabular-nums">{s.latency_p95_ms === null ? "—" : `${s.latency_p95_ms.toLocaleString()} ms`}</td>
                    <td className={`text-right tabular-nums ${ratio && ratio >= 2 ? "text-[#f3ab8e]" : "text-slate-400"}`}>
                      {ratio === null ? "—" : `${ratio >= 1 ? "+" : ""}${((ratio - 1) * 100).toFixed(0)}%`}
                    </td>
                    <td className="text-right tabular-nums text-slate-400">{s.active_incidents}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

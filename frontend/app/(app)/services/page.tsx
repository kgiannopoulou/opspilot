"use client";

import { Sparkline } from "@/components/charts";
import { Card, ErrorBox, HealthPill, Spinner } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import type { DashboardSummary, ServiceOut } from "@/services/api";

export default function ServicesPage() {
  const catalog = useApi<ServiceOut[]>("/services");
  const health = useApi<DashboardSummary>("/dashboard/summary", { pollMs: 30_000 });

  if (catalog.loading && !catalog.data) return <Spinner />;
  if (catalog.error) return <ErrorBox message={catalog.error} />;
  const byName = Object.fromEntries((health.data?.services ?? []).map((s) => [s.name, s]));

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Service catalogue</h1>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {catalog.data?.map((s) => {
          const h = byName[s.name];
          return (
            <Card key={s.name} title={<span className="normal-case tracking-normal text-slate-200">{s.name}</span>}
              action={h && <HealthPill status={h.status} />}>
              <p className="text-sm text-slate-300">{s.description}</p>
              <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
                <div><dt className="text-slate-500">Owner</dt><dd>{s.owner_team}</dd></div>
                <div><dt className="text-slate-500">Tier</dt><dd>{s.tier}</dd></div>
                <div><dt className="text-slate-500">Error rate</dt>
                  <dd className="tabular-nums">{h?.error_rate == null ? "—" : `${(h.error_rate * 100).toFixed(1)}%`}</dd></div>
                <div><dt className="text-slate-500">p95 latency</dt>
                  <dd className="tabular-nums">{h?.latency_p95_ms == null ? "—" : `${h.latency_p95_ms.toLocaleString()} ms`}</dd></div>
              </dl>
              {h && h.error_rate_series.length > 1 && (
                <div className="mt-3"><div className="mb-1 text-[11px] text-slate-500">Error rate, 6 h</div>
                  <Sparkline values={h.error_rate_series.map((p) => p.error_rate)} width={260} /></div>
              )}
              <div className="mt-3 text-xs text-slate-500">
                Depends on: {s.dependencies.length ? s.dependencies.join(", ") : "—"}
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

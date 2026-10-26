"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Card } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import { post, type IncidentDetail, type ServiceOut, type Severity } from "@/services/api";

const SEVERITIES: [Severity, string][] = [["SEV1", "Critical"], ["SEV2", "Major"], ["SEV3", "Minor"], ["SEV4", "Low"]];

/** Declare an incident: you become its commander, and the incident page opens ready to investigate. */
export function DeclareIncident({ onCancel }: { onCancel: () => void }) {
  const router = useRouter();
  const services = useApi<ServiceOut[]>("/services");
  const [title, setTitle] = useState("");
  const [severity, setSeverity] = useState<Severity>("SEV2");
  const [service, setService] = useState("payment-service");
  const [minutesAgo, setMinutesAgo] = useState(15);
  const [summary, setSummary] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const started_at = new Date(Date.now() - minutesAgo * 60_000).toISOString();
      const incident = await post<IncidentDetail>("/incidents", { title, severity, primary_service: service, started_at, summary });
      router.push(`/incidents/${incident.number}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  const field = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm";
  return (
    <Card title="Declare incident">
      <form onSubmit={submit} className="grid gap-3 md:grid-cols-4">
        <label className="text-xs text-slate-400 md:col-span-2">Title
          <input required minLength={3} maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} className={field} placeholder="Checkout failing for many customers" />
        </label>
        <label className="text-xs text-slate-400">Severity
          <select value={severity} onChange={(e) => setSeverity(e.target.value as Severity)} className={field}>
            {SEVERITIES.map(([value, label]) => <option key={value} value={value}>{value} · {label}</option>)}
          </select>
        </label>
        <label className="text-xs text-slate-400">Affected service
          <select value={service} onChange={(e) => setService(e.target.value)} className={field}>
            {(services.data ?? []).map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
          </select>
        </label>
        <label className="text-xs text-slate-400 md:col-span-3">Summary
          <input maxLength={5000} value={summary} onChange={(e) => setSummary(e.target.value)} className={field} placeholder="What customers report" />
        </label>
        <label className="text-xs text-slate-400">Started (minutes ago)
          <input type="number" min={0} max={1440} value={minutesAgo} onChange={(e) => setMinutesAgo(Number(e.target.value))} className={field} />
        </label>
        <div className="flex items-center gap-2 md:col-span-4">
          <button type="submit" disabled={busy} className="rounded-md bg-sky-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-sky-400 disabled:opacity-60">
            {busy ? "Declaring…" : "Declare incident"}
          </button>
          <button type="button" onClick={onCancel} className="rounded-md border border-slate-700 px-3 py-1.5 text-xs">Cancel</button>
          {error && <span className="text-xs text-[#f08a8a]">{error}</span>}
        </div>
      </form>
    </Card>
  );
}

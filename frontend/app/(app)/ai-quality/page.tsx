"use client";

import Link from "next/link";
import { REASON_LABEL } from "@/components/InvestigationFeedback";
import { Card, ErrorBox, Spinner, relativeTime } from "@/components/ui";
import { useApi } from "@/hooks/useApi";
import type { AIQualityReport, FeedbackReason } from "@/services/api";

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);

function Tile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-lg border border-slate-800 px-3 py-2">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className="text-xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-[11px] text-slate-500">{hint}</div>}
    </div>
  );
}

function RateTable({ title, rows }: { title: string; rows: Record<string, { useful: number; incorrect: number; useful_rate: number }> }) {
  const entries = Object.entries(rows);
  return (
    <div>
      <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-slate-500">{title}</div>
      {entries.length === 0 ? <p className="text-sm text-slate-500">No ratings yet.</p> : (
        <table className="w-full text-xs">
          <tbody className="divide-y divide-slate-800/70">
            {entries.map(([name, r]) => (
              <tr key={name}>
                <td className="py-1.5 font-mono text-slate-300">{name}</td>
                <td className="text-right tabular-nums text-slate-400">👍 {r.useful} · 👎 {r.incorrect}</td>
                <td className="w-28 pl-3">
                  <div className="h-1.5 overflow-hidden rounded-full bg-slate-800" role="meter" aria-valuenow={Math.round(r.useful_rate * 100)} aria-label={`${name} useful rate`}>
                    <div className="h-full rounded-full bg-sky-400" style={{ width: pct(r.useful_rate) }} />
                  </div>
                </td>
                <td className="w-12 text-right tabular-nums">{pct(r.useful_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function AIQualityPage() {
  const { data, error } = useApi<AIQualityReport>("/ai/quality", { pollMs: 60_000 });
  if (error) return <ErrorBox message={error} />;
  if (!data) return <Spinner />;
  const inv = data.investigations;
  const triage = data.triage;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">AI quality</h1>
        <p className="text-sm text-slate-400">
          What analysts think of the AI in practice — the number to watch before trusting a new model, prompt or engine version.
          Offline benchmark results are in <span className="font-mono">docs/evaluation</span>.
        </p>
      </div>

      <Card title="Investigations · analyst feedback">
        <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-5">
          <Tile label="Useful rate" value={pct(inv.useful_rate)} hint={`${inv.votes} votes`} />
          <Tile label="Rated investigations" value={`${inv.rated} / ${inv.completed}`} hint="completed runs" />
          <Tile label="Grounding passed" value={`${inv.grounding_passed} / ${inv.completed}`} hint="every claim cites evidence" />
          <Tile label="LLM drafts rejected" value={`${inv.llm_drafts_rejected} / ${inv.llm_drafts}`} hint="by the grounding check" />
          <Tile label="Failed runs" value={inv.failed} />
        </div>
        <div className="grid gap-6 lg:grid-cols-3">
          <RateTable title="By engine" rows={inv.by_engine} />
          <RateTable title="By diagnosis" rows={inv.by_hypothesis} />
          <div>
            <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-slate-500">Why 👎</div>
            {Object.keys(inv.reasons).length === 0 ? <p className="text-sm text-slate-500">No negative feedback.</p> : (
              <ul className="space-y-1 text-xs">
                {Object.entries(inv.reasons).sort((a, b) => b[1] - a[1]).map(([reason, n]) => (
                  <li key={reason} className="flex justify-between"><span className="text-slate-300">{REASON_LABEL[reason as FeedbackReason] ?? reason}</span><span className="tabular-nums text-slate-400">{n}</span></li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Triage suggestions · human review">
          <div className="mb-4 grid grid-cols-3 gap-3">
            <Tile label="Accepted as-is" value={pct(triage.acceptance_rate)} hint="of reviewed suggestions" />
            <Tile label="Reviewed" value={(triage.by_status.ACCEPTED ?? 0) + (triage.by_status.MODIFIED ?? 0) + (triage.by_status.REJECTED ?? 0)} />
            <Tile label="Awaiting review" value={triage.by_status.PENDING ?? 0} />
          </div>
          <div className="text-xs text-slate-400">
            Corrections by field: {Object.entries(triage.corrections).map(([f, n]) => `${f} ${n}`).join(" · ")}
          </div>
        </Card>

        <Card title="Recent 👎 with comments">
          {data.recent_negative.length === 0 ? <p className="text-sm text-slate-500">Nothing yet.</p> : (
            <ul className="space-y-3">
              {data.recent_negative.map((n) => (
                <li key={`${n.investigation_id}-${n.user}`} className="text-sm">
                  <div className="flex flex-wrap items-baseline gap-2 text-xs">
                    <Link href={`/incidents/${n.incident}`} className="font-mono text-sky-300 hover:underline">{n.incident}</Link>
                    <span className="text-slate-400">#{n.investigation_id} · {n.hypothesis}</span>
                    <span className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-300">{REASON_LABEL[n.reason as FeedbackReason] ?? n.reason}</span>
                    <span className="ml-auto text-slate-500">{n.user} · {relativeTime(n.at)}</span>
                  </div>
                  {n.comment && <p className="mt-1 text-slate-300">“{n.comment}”</p>}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

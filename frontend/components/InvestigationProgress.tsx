"use client";

import { useEffect, useState } from "react";
import type { Investigation } from "@/services/api";

const ICON: Record<string, string> = { done: "✓", running: "", failed: "✕", pending: "" };

/** Live view of a queued/running investigation: the worker reports each pipeline stage as it starts and ends. */
export function InvestigationProgress({ inv }: { inv: Investigation }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 250); return () => clearInterval(id); }, []);

  const stages = inv.progress;
  const done = stages.filter((s) => s.status === "done").length;
  const running = stages.find((s) => s.status === "running");
  const pct = stages.length ? Math.round(((done + (running ? 0.5 : 0)) / stages.length) * 100) : 0;
  const since = new Date(inv.started_at ?? inv.created_at).getTime();
  const elapsed = Math.max(0, (now - since) / 1000);

  return (
    <div className="py-2">
      <div className="flex items-baseline justify-between text-sm">
        <span className="font-medium text-slate-100">
          {inv.status === "QUEUED" ? (inv.attempts > 0 ? "Retrying…" : "Queued — waiting for a worker…") : `${running?.label ?? "Processing"}…`}
        </span>
        <span className="tabular-nums text-xs text-slate-500">{elapsed.toFixed(1)} s</span>
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-800" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}
        aria-label="Investigation progress">
        <div className="h-full rounded-full bg-sky-400 transition-[width] duration-500" style={{ width: `${pct}%` }} />
      </div>
      <ol className="mt-4 space-y-1.5 text-sm">
        {stages.map((s) => (
          <li key={s.key} className="flex items-center gap-2.5">
            <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${
              s.status === "done" ? "bg-[#0ca30c]/20 text-[#5fd35f]"
                : s.status === "failed" ? "bg-[#d03b3b]/20 text-[#f08a8a]"
                : s.status === "running" ? "bg-sky-500/20 text-sky-300" : "bg-slate-800 text-slate-600"}`}>
              {s.status === "running" ? <span className="h-2 w-2 animate-ping rounded-full bg-sky-300" /> : ICON[s.status]}
            </span>
            <span className={s.status === "pending" ? "text-slate-500" : "text-slate-200"}>{s.label}</span>
            {s.started_at && s.finished_at && (
              <span className="ml-auto tabular-nums text-[11px] text-slate-500">
                {Math.max(0, new Date(s.finished_at).getTime() - new Date(s.started_at).getTime())} ms
              </span>
            )}
          </li>
        ))}
      </ol>
      {inv.error && <p className="mt-3 text-xs text-[#fcd27a]">Previous attempt failed: {inv.error}</p>}
      <p className="mt-4 text-[11px] text-slate-500">
        Running in a background worker (Redis Streams queue). The API answered immediately; this panel polls for progress.
      </p>
    </div>
  );
}

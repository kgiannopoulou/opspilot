"use client";

import { useState } from "react";
import { useApi } from "@/hooks/useApi";
import { api, del, type FeedbackReason, type FeedbackSummary } from "@/services/api";

export const REASON_LABEL: Record<FeedbackReason, string> = {
  WRONG_ROOT_CAUSE: "Wrong root cause",
  MISSING_EVIDENCE: "Missed important evidence",
  IRRELEVANT_DOCUMENTS: "Irrelevant documentation",
  UNHELPFUL_STEPS: "Steps not helpful",
  WRONG_QUERY: "Query wrong or not useful",
  UNSUPPORTED_CLAIM: "Claim not backed by evidence",
  OTHER: "Other",
};

/** "Was this investigation useful?" — one verdict per person, changeable; 👎 asks why so it can be acted on. */
export function InvestigationFeedback({ incident, investigationId, canRate }: {
  incident: string; investigationId: number; canRate: boolean;
}) {
  const path = `/incidents/${incident}/investigations/${investigationId}/feedback`;
  const { data, reload } = useApi<FeedbackSummary>(path);
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState<FeedbackReason>("WRONG_ROOT_CAUSE");
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const mine = data?.mine?.rating;

  async function send(rating: 1 | -1) {
    setError(null);
    try {
      await api(path, { method: "PUT", body: JSON.stringify(rating === 1 ? { rating } : { rating, reason, comment }) });
      setAsking(false);
      await reload();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }
  async function withdraw() {
    await del(path);
    await reload();
  }

  const button = (active: boolean) =>
    `rounded-md px-2.5 py-1 text-xs ring-1 transition ${active ? "bg-sky-500/20 text-sky-200 ring-sky-500/60" : "text-slate-300 ring-slate-700 hover:bg-slate-800"}`;

  return (
    <div className="mt-5 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-slate-300">Was this investigation useful?</span>
        {canRate && (
          <>
            <button onClick={() => send(1)} className={button(mine === 1)} aria-pressed={mine === 1}>👍 Useful</button>
            <button onClick={() => setAsking((a) => !a)} className={button(mine === -1)} aria-pressed={mine === -1}>👎 Incorrect</button>
            {mine !== undefined && <button onClick={withdraw} className="text-slate-500 hover:text-slate-300">undo</button>}
          </>
        )}
        {data && (
          <span className="ml-auto text-slate-500">
            {data.useful} useful · {data.incorrect} incorrect
            {mine === -1 && data.mine?.reason && ` · you said: ${REASON_LABEL[data.mine.reason]}`}
          </span>
        )}
      </div>
      {asking && (
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <label className="text-[11px] text-slate-400">What was wrong?
            <select value={reason} onChange={(e) => setReason(e.target.value as FeedbackReason)}
              className="mt-0.5 block rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200">
              {Object.entries(REASON_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <input value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000} placeholder="Optional: what should it have said?"
            aria-label="Comment" className="min-w-[220px] flex-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs" />
          <button onClick={() => send(-1)} className="rounded-md bg-sky-500 px-3 py-1 text-xs font-semibold text-slate-950 hover:bg-sky-400">Submit</button>
        </div>
      )}
      {error && <div className="mt-1 text-xs text-[#f08a8a]">{error}</div>}
    </div>
  );
}

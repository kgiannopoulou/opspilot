"use client";

import { useEffect, useState } from "react";
import { api, openStream, type Investigation } from "@/services/api";
import { readEvents } from "@/services/sse";

/** Live progress of a queued/running investigation: Server-Sent Events while the stream works, polling every
 *  second if it cannot be opened or drops (ADR 004). Returns the latest state for `id`, or null. */
export function useInvestigationStream(incident: string, id: number | null): Investigation | null {
  const [latest, setLatest] = useState<Investigation | null>(null);

  useEffect(() => {
    if (id === null) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setInterval> | undefined;
    const base = `/incidents/${incident}/investigations/${id}`;
    const poll = () => {
      timer ??= setInterval(() => {
        api<Investigation>(base).then(setLatest, () => undefined);
      }, 1_000);
    };
    (async () => {
      try {
        const res = await openStream(`${base}/events`, controller.signal);
        for await (const message of readEvents(res.body!)) {
          if (message.event === "progress") setLatest(JSON.parse(message.data) as Investigation);
          if (message.event === "done") return;
          if (message.event === "timeout") break;
        }
        if (!controller.signal.aborted) poll(); // stream ended early: keep the page live
      } catch {
        if (!controller.signal.aborted) poll();
      }
    })();
    return () => { controller.abort(); if (timer) clearInterval(timer); };
  }, [incident, id]);

  return latest && latest.id === id ? latest : null;
}

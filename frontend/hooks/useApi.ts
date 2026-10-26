"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/services/api";

interface Options {
  /** Re-fetch every N ms while the tab is visible. */
  pollMs?: number;
}

interface Result<T> { path: string | null; data: T | null; error: string | null }

/** Minimal data-fetching hook: loading/error state, manual reload, optional polling.
 *  While a new path loads, the previous data stays visible (no flash of empty content); `loading` is derived from
 *  whether the current path has answered yet, so no state is set synchronously during render or effects. */
export function useApi<T>(path: string | null, { pollMs }: Options = {}) {
  const [result, setResult] = useState<Result<T>>({ path: null, data: null, error: null });
  const latest = useRef(path);
  useEffect(() => { latest.current = path; }, [path]);

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const data = await api<T>(path);
      if (latest.current === path) setResult({ path, data, error: null });
    } catch (e) {
      const error = e instanceof Error ? e.message : String(e);
      if (latest.current === path) setResult((r) => ({ path, data: r.data, error }));
    }
  }, [path]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- state is set when the request resolves, not synchronously
    void load();
    if (!pollMs) return;
    const id = setInterval(() => { if (document.visibilityState === "visible") void load(); }, pollMs);
    return () => clearInterval(id);
  }, [load, pollMs]);

  const answered = result.path === path;
  return {
    data: result.data,
    error: answered ? result.error : null,
    loading: path !== null && !answered,
    reload: load,
  };
}

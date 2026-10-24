"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/services/api";

interface Options {
  /** Re-fetch every N ms while the tab is visible. */
  pollMs?: number;
}

/** Minimal data-fetching hook: loading/error state, manual reload, optional polling. */
export function useApi<T>(path: string | null, { pollMs }: Options = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const latest = useRef(path);
  latest.current = path;

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const result = await api<T>(path);
      if (latest.current === path) { setData(result); setError(null); }
    } catch (e) {
      if (latest.current === path) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (latest.current === path) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setLoading(path !== null);
    void load();
    if (!pollMs) return;
    const id = setInterval(() => { if (document.visibilityState === "visible") void load(); }, pollMs);
    return () => clearInterval(id);
  }, [load, path, pollMs]);

  return { data, error, loading, reload: load };
}

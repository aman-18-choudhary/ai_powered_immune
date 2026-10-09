import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, friendlyError } from "@/lib/api/client";

export interface RestState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  forbidden: boolean;
  reload: () => void;
}

/**
 * Fetch on mount and whenever `key` changes (debounced). Aborts in-flight requests, keeps the
 * previous data while reloading (no flash of empty state).
 */
export function useRest<T>(fetcher: (signal: AbortSignal) => Promise<T>, key: string | number, debounceMs = 250, enabled = true): RestState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [loading, setLoading] = useState(enabled);
  const [nonce, setNonce] = useState(0);
  const fetchRef = useRef(fetcher);
  fetchRef.current = fetcher;
  const first = useRef(true);

  useEffect(() => {
    if (!enabled) return;
    const ctl = new AbortController();
    const run = async () => {
      setLoading(true);
      try {
        const v = await fetchRef.current(ctl.signal);
        if (ctl.signal.aborted) return;
        setData(v);
        setError(null);
        setForbidden(false);
      } catch (e) {
        if (ctl.signal.aborted) return;
        if (e instanceof ApiError && e.code === "aborted") return;
        setError(friendlyError(e));
        setForbidden(e instanceof ApiError && e.code === "forbidden");
      } finally {
        if (!ctl.signal.aborted) setLoading(false);
      }
    };
    const wait = first.current ? 0 : debounceMs;
    first.current = false;
    const h = setTimeout(run, wait);
    return () => {
      clearTimeout(h);
      ctl.abort();
    };
  }, [key, nonce, debounceMs, enabled]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, loading, error, forbidden, reload };
}

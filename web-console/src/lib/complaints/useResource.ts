import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { messageFor } from "./api";

export interface Resource<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  /** HTTP status of the last failure (0 = network). */
  errorStatus: number | null;
  /** The service could not be reached or answered 503. */
  unavailable: boolean;
  reload: () => void;
  setData: (v: T) => void;
}

/** Fetch on mount and whenever `key` changes; aborts stale requests; keeps old data while reloading. */
export function useResource<T>(fetcher: (signal: AbortSignal) => Promise<T>, key: string, enabled = true): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [nonce, setNonce] = useState(0);
  const ref = useRef(fetcher);
  ref.current = fetcher;

  useEffect(() => {
    if (!enabled) return;
    const ctl = new AbortController();
    setLoading(true);
    ref
      .current(ctl.signal)
      .then((v) => {
        if (ctl.signal.aborted) return;
        setData(v);
        setError(null);
        setErrorStatus(null);
      })
      .catch((e: unknown) => {
        if (ctl.signal.aborted || (e instanceof ApiError && e.code === "aborted")) return;
        setError(messageFor(e));
        setErrorStatus(e instanceof ApiError ? e.status : null);
      })
      .finally(() => {
        if (!ctl.signal.aborted) setLoading(false);
      });
    return () => ctl.abort();
  }, [key, nonce, enabled]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const unavailable = errorStatus === 0 || (errorStatus !== null && errorStatus >= 500);
  return { data, loading, error, errorStatus, unavailable, reload, setData };
}
